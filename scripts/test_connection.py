"""Quick connection test for the MQ Actor client.

Run with MQ loaded in-game:
    python scripts/test_connection.py

What this tests:
    1. Can we open \\.\pipe\mqpipe?
    2. Can we send AddIdentity without erroring?
    3. Does the pipe stay open (MQ didn't reject us)?
"""

import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")

# allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from mq_actor import MQActorClient, MQNotConnectedError


async def main():
    cfg = load_config()

    print(f"Connecting to {cfg.pipe_name} ...")
    client = MQActorClient(cfg.pipe_name, cfg.actor_name, cfg.actor_mailbox)

    try:
        client.connect()
    except Exception as e:
        print(f"FAIL — could not connect: {e}")
        print("Is MQ running with a character loaded?")
        return

    print("Connected and identity sent.")
    print("Waiting 2s to confirm pipe stays open ...")
    await asyncio.sleep(2)

    if not client.is_connected():
        print("FAIL — pipe closed after identity send (MQ may have rejected us)")
        return

    print("OK — pipe is stable.")
    print()
    print("Now testing RPC call to 'mq-mcp' mailbox ...")
    print("(make sure mq-mcp.lua is running in-game: /lua run mq-mcp)")
    print()

    try:
        result = await client.call("mq-mcp", {"type": "get_character"}, timeout=5.0)
        print("SUCCESS — got character response:")
        import json
        print(json.dumps(result, indent=2))
    except asyncio.TimeoutError:
        print("TIMEOUT — connected to MQ pipe but mq-mcp.lua is not responding.")
        print("Check that the Lua script is running: /lua run mq-mcp")
    except MQNotConnectedError as e:
        print(f"FAIL — lost connection during RPC: {e}")
    finally:
        client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
