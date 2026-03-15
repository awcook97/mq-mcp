"""Quick end-to-end test: call get_character and print the response."""
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from mq_actor import MQActorClient

async def main():
    cfg = load_config()
    client = MQActorClient(cfg.pipe_name, cfg.actor_name, cfg.actor_mailbox)
    client.connect()
    await asyncio.sleep(0.5)

    mailbox = cfg.lua_mailbox
    print(f"\nCalling get_character via mailbox={mailbox!r} ...\n")
    try:
        result = await client.call(mailbox, {"type": "get_character"}, timeout=5.0)
        print("get_character response:")
        for k, v in (result.items() if isinstance(result, dict) else [("result", result)]):
            print(f"  {k}: {v}")
    except asyncio.TimeoutError:
        print("TIMEOUT — Lua handler did not reply within 5s")
    except Exception as e:
        print(f"ERROR: {e}")

    client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
