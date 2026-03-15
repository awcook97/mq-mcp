# mq-mcp

An MCP (Model Context Protocol) server that connects Claude to a running MacroQuest instance for Lua scripting assistance. Gives Claude accurate, live context about your game state so it can write MQ Lua scripts that actually work.

## Features

**Live game state via Actor RPC**
- Character name, class, level, and spell gems
- Full spell book
- Currently loaded MQ plugins

**Accurate TLO/type reference**
- Runtime introspection via the `Type` TLO — reflects your exact MQ version and loaded plugins
- Enriched with [mq-definitions](https://github.com/macroquest/mq-definitions) docs (member descriptions, types, signatures)
- Cached in-process; refresh after loading/unloading plugins

**Script access**
- List, read, and write Lua scripts directly in your MQ lua directory

## How it works

```
Claude (MCP client)
    ↕  MCP / stdio
Python MCP server  (server.py)
    ↕  Named pipe \\.\pipe\mqpipe + protobuf Actor RPC
mq-mcp.lua  (running in-game via /lua run mq-mcp)
    ↕  mq.TLO.*
EverQuest game state
```

A Lua script runs inside MacroQuest and registers as an Actor mailbox (`lua:mq-mcp:mq-mcp`). The Python server connects to MQ's named pipe and makes RPC calls into that mailbox on demand. Claude talks to the server over stdio.

## Requirements

- MacroQuest (live or emu)
- Python 3.11+
- Claude Desktop or Claude Code

## Setup

### 1. Clone and install

```
git clone https://github.com/your-username/mq-mcp
cd mq-mcp
python -m venv .venv
.venv\Scripts\pip install -e .
```

### 2. Configure

Copy `config.json.example` to `config.json` and set your paths:

```json
{
    "mq_lua_path": "C:\\Users\\you\\AppData\\Local\\YourServer\\Emu\\Release\\lua",
    "mq_definitions_path": null,
    "pipe_name": "\\\\.\\pipe\\mqpipe",
    "actor_name": "mcp-server",
    "actor_mailbox": "scripting-assistant",
    "lua_mailbox": "lua:mq-mcp:mq-mcp",
    "max_member_scan": 2000,
    "rpc_timeout": 10.0
}
```

- `mq_lua_path` — your MQ `lua/` directory (where scripts live)
- `mq_definitions_path` — optional path to a local [mq-definitions](https://github.com/macroquest/mq-definitions) clone for richer docs. Set to `null` to use the bundled copy.

### 3. Install the in-game Lua script

Copy `lua/mq-mcp.lua` to your MQ lua directory:

```
copy lua\mq-mcp.lua "C:\Users\you\...\lua\mq-mcp.lua"
```

### 4. Start the Lua script in-game

```
/lua run mq-mcp
```

You should see `[mq-mcp] Registered Actor mailbox 'mq-mcp'` in the MQ overlay. Leave it running.

### 5. Add to Claude

**Claude Code:**
```
claude mcp add mq-mcp -- C:\path\to\mq-mcp\.venv\Scripts\python C:\path\to\mq-mcp\server.py
```

**Claude Desktop** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "mq-mcp": {
      "command": "C:\\path\\to\\mq-mcp\\.venv\\Scripts\\python",
      "args": ["C:\\path\\to\\mq-mcp\\server.py"]
    }
  }
}
```

The server connects to MQ automatically on startup. If MQ isn't running, the script/filesystem tools still work — only the game state RPC tools will fail.

## Available Tools

| Tool | Description |
|---|---|
| `mq_eval(code)` | Execute any Lua code in the MQ environment and return the result |
| `get_tlo_reference` | Full runtime TLO type map enriched with mq-definitions docs |
| `refresh_tlo_types` | Re-run TLO introspection (use after loading/unloading plugins) |
| `list_scripts` | List all `.lua` files in your MQ lua directory |
| `read_script` | Read a Lua script by name |
| `write_script` | Write a Lua script directly to your MQ lua directory |

### mq_eval examples

```lua
-- Query any TLO value
mq.TLO.Me.Name()
mq.TLO.Me.Level()
mq.TLO.Target.Distance()

-- Build a table of results
return { name=mq.TLO.Me.Name(), class=mq.TLO.Me.Class.Name(), level=mq.TLO.Me.Level() }

-- Run a command
mq.cmd('/echo hello from claude')

-- More complex queries
local gems = {}
for i = 1, 13 do
    local s = mq.TLO.Me.Gem(i)()
    if s then gems[i] = s end
end
return gems
```
