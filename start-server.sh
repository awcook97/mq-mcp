#!/bin/bash
export WINEPREFIX=/home/andrew/Games/eqlive
cd /home/andrew/Games/mq-mcp
exec /home/andrew/.local/bin/uv run server.py "$@"
