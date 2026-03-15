"""Test multiple address variants to find what routes to the Lua actor.

Run with MQ loaded and mq-mcp.lua running:
    python scripts/test_routing.py
"""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from mq_actor import MQActorClient, _dict_to_variant
from proto import Routing_pb2

cfg = load_config()
_PAYLOAD = _dict_to_variant({"type": "get_character"}).SerializeToString()
_TIMEOUT = 3.0

def make_envelope(client, seq, no_return_mailbox=False, simple_mode=False, **address_fields):
    """Build an Envelope with the given address fields."""
    env = Routing_pb2.Envelope()

    # container
    if 'pid' in address_fields:
        env.address.process.pid = address_fields['pid']

    # address oneof
    if 'name' in address_fields:
        env.address.name = address_fields['name']
    elif 'character' in address_fields:
        env.address.client.character = address_fields['character']
        if 'server' in address_fields:
            env.address.client.server = address_fields['server']
        if 'account' in address_fields:
            env.address.client.account = address_fields['account']

    # mailbox
    if 'mailbox' in address_fields:
        env.address.mailbox = address_fields['mailbox']

    # uuid
    if 'uuid' in address_fields:
        env.address.uuid = address_fields['uuid']

    # return address
    env.return_address.name = client._actor_name
    if not no_return_mailbox:
        env.return_address.mailbox = client._actor_mailbox
    env.return_address.process.pid = os.getpid()

    env.mode     = 0 if simple_mode else 1   # 0=SimpleMessage, 1=CallAndResponse
    env.sequence = seq
    env.payload  = _PAYLOAD
    return env


async def try_variant(client, label, no_return_mailbox=False, simple_mode=False, **address_fields):
    """Send one RPC variant and wait for a response."""
    seq    = client._next_seq()
    loop   = asyncio.get_running_loop()
    future = loop.create_future()

    with client._pending_lock:
        client._pending[seq] = (loop, future)

    env = make_envelope(client, seq, no_return_mailbox=no_return_mailbox,
                        simple_mode=simple_mode, **address_fields)
    mode_val = 0 if simple_mode else 1
    client._send_raw(2, mode_val, seq, env.SerializeToString())   # MSG_ROUTE

    try:
        result = await asyncio.wait_for(future, timeout=_TIMEOUT)
        logging.info("✓ SUCCESS  [%s] — got reply", label)
        return True
    except asyncio.TimeoutError:
        with client._pending_lock:
            client._pending.pop(seq, None)
        logging.info("✗ timeout  [%s]", label)
        return False


async def main():
    client = MQActorClient(cfg.pipe_name, cfg.actor_name, cfg.actor_mailbox)
    client.connect()
    await asyncio.sleep(0.5)   # let identity responses settle

    pid  = client._mq_pid
    uid  = client._mq_uuid
    logging.info("MQ PID: %s  UUID: %s", pid, uid)

    # Round 1: UUID-based variants (MacroView's preferred method)
    logging.info("=== Round 1: UUID-based routing ===")
    uuid_variants = [
        ("uuid+mailbox=lua:mq-mcp:mq-mcp",     dict(uuid=uid, mailbox="lua:mq-mcp:mq-mcp")),
        ("uuid+mailbox=MQ2Lua:mq-mcp:mq-mcp", dict(uuid=uid, mailbox="MQ2Lua:mq-mcp:mq-mcp")),
        ("uuid+mailbox=mq-mcp:mq-mcp",         dict(uuid=uid, mailbox="mq-mcp:mq-mcp")),
        ("uuid+mailbox=mq-mcp",                dict(uuid=uid, mailbox="mq-mcp")),
        ("uuid only (no mailbox)",             dict(uuid=uid)),
    ] if uid else []

    for label, fields in uuid_variants:
        await try_variant(client, label, **fields)
        await asyncio.sleep(0.2)

    # Round 2: PID + mailbox variants
    logging.info("=== Round 2: PID-based routing ===")
    variants = [
        ("mailbox=mq-mcp (no pid)",            dict(mailbox="mq-mcp")),
        ("mailbox=mq-mcp:mq-mcp (no pid)",     dict(mailbox="mq-mcp:mq-mcp")),
        ("pid+mailbox=mq-mcp",                 dict(pid=pid, mailbox="mq-mcp")),
        ("pid+mailbox=mq-mcp:mq-mcp",          dict(pid=pid, mailbox="mq-mcp:mq-mcp")),
        ("pid+name=mq-mcp",                    dict(pid=pid, name="mq-mcp")),
        ("name=mq-mcp (no pid)",               dict(name="mq-mcp")),
        ("character=Kramps+mailbox=mq-mcp",    dict(character="Kramps", server="Karana", mailbox="mq-mcp")),
        ("character=Kramps+mailbox=mq-mcp:mq-mcp", dict(character="Kramps", server="Karana", mailbox="mq-mcp:mq-mcp")),
    ]

    for label, fields in variants:
        await try_variant(client, label, **fields)
        await asyncio.sleep(0.2)

    # Round 2: same variants WITHOUT return_address.mailbox
    logging.info("=== Round 2: WITHOUT return_address.mailbox ===")
    for label, fields in variants:
        await try_variant(client, f"[no-ret-mb] {label}", no_return_mailbox=True, **fields)
        await asyncio.sleep(0.2)

    # Round 3: SIMPLE mode (fire-and-forget) — watch in-game for [mq-mcp] output
    # No reply expected from Python; delivery confirmed only by in-game print.
    logging.info("=== Round 3: SIMPLE mode (check in-game output) ===")
    simple_variants = [
        ("simple pid+mailbox=mq-mcp",       dict(pid=pid, mailbox="mq-mcp")),
        ("simple pid+mailbox=mq-mcp:mq-mcp",dict(pid=pid, mailbox="mq-mcp:mq-mcp")),
        ("simple mailbox=mq-mcp (no pid)",  dict(mailbox="mq-mcp")),
    ]
    for label, fields in simple_variants:
        seq = client._next_seq()
        env = make_envelope(client, seq, simple_mode=True, **fields)
        client._send_raw(2, 0, seq, env.SerializeToString())
        logging.info("sent [%s]", label)
        await asyncio.sleep(1.0)   # pause so any in-game echo is visible

    client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
