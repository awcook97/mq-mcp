"""Listen for incoming messages from MQ/Lua actors.

Run this BEFORE (re)starting mq-mcp.lua in-game.
It will print any incoming ROUTE messages, especially the startup announce.

    python scripts/listen.py
"""
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from mq_actor import MQActorClient


async def main():
    cfg = load_config()
    client = MQActorClient(cfg.pipe_name, cfg.actor_name, cfg.actor_mailbox)
    client.connect()
    print("Listening for messages. Restart mq-mcp.lua in-game now (/lua stop mq-mcp && /lua run mq-mcp).")
    print("Ctrl-C to quit.\n")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
