# mq-mcp

An MCP (Model Context Protocol) server that connects Claude to a running MacroQuest instance for Lua scripting assistance.

## What it does

Gives Claude accurate context about your game state so it can write MQ Lua scripts that actually work:

- Live character data — class, level, spell gems, loaded plugins
- Accurate TLO/type reference — pulled from the running MQ instance and enriched with mq-definitions, so Claude knows the real member names and signatures instead of guessing
- Script access — read existing Lua scripts for context, write new ones directly to your MQ lua directory

## How it works

A Lua script runs inside MacroQuest and registers as an Actor mailbox. The Python MCP server connects to MQ's named pipe (`\\.\pipe\mqpipe`) and makes RPC calls into that mailbox to query game state on demand. Claude talks to the MCP server over stdio.

## Status

Early development. See [DESIGN.md](DESIGN.md) and [TASKS.md](TASKS.md).

## Requirements

- MacroQuest (live or emu)
- Python 3.11+
- Claude Desktop or Claude Code

## Setup

Documentation will be added as the project takes shape.
