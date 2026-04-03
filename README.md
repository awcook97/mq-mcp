# mq-mcp

An MCP (Model Context Protocol) server that connects Claude to a running MacroQuest instance for Lua scripting assistance. Gives Claude live access to your game state so it can write MQ Lua scripts that actually work.

## Features

**Execute any Lua code in-game**
Claude can run arbitrary Lua in the MQ environment via `mq_eval` — query any TLO, run commands, read state, or do anything the MQ Lua API supports. No fixed set of queries; if MQ can do it, Claude can ask for it.

**Accurate TLO/type reference**
Runtime introspection via the `Type` TLO gives Claude the exact member list for every type registered in your running MQ instance, including plugin-registered types. Enriched with [mq-definitions](https://github.com/macroquest/mq-definitions) docs for descriptions and signatures.

**Script access**
List, read, and write Lua scripts directly in your MQ lua directory — Claude can read your existing scripts for context and write new ones straight to disk.

## How it works

```
Claude (MCP client)
    ↕  MCP / stdio
Python MCP server  (server.py)
    ↕  Named pipe \\.\pipe\mqpipe + protobuf Actor RPC
mq-mcp.lua  (running in-game via /lua run mq-mcp)
    ↕  mq.TLO.* / mq.cmd()
EverQuest game state
```

A Lua script runs inside MacroQuest and registers as an Actor mailbox. The Python server connects to MQ's named pipe and makes RPC calls into that mailbox on demand. Claude talks to the server over stdio.

## Requirements

- MacroQuest (live or emu)
- Python 3.11+
- Claude Desktop or Claude Code

## Setup

### 1. Clone and install

```
git clone https://github.com/johnfking/mq-mcp
cd mq-mcp
python -m venv .venv
.venv\Scripts\pip install -e .
```

### 2. Configure

Copy `config.json.example` to `config.json` and set your MQ lua path:

```json
{
    "mq_lua_path": "path\\to\\your\\mq\\lua"
}
```

That's the only required change. mq-definitions are optional but recommended — they add member descriptions, types, and signatures to the TLO reference. Claude can download and configure them for you automatically (it will ask when you first use `get_tlo_reference`).

### 3. Install the in-game Lua script

Copy `lua/mq-mcp.lua` to your MQ lua directory (the same directory as `mq_lua_path`). You'll need to re-copy it whenever you update mq-mcp.

### 4. Start the Lua script in-game

```
/lua run mq-mcp
```

You should see `[mq-mcp] Registered Actor mailbox 'mq-mcp'` in the MQ overlay. Leave it running — it needs to be active for game state tools to work.

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

The server connects to MQ automatically on startup. If MQ isn't running, the script/filesystem tools still work — only `mq_eval` and the TLO reference tools will fail.

### 5.1 Add to Copilot CLI (optional)

**Copilot CLI** - add to `~/.copilot/mcp-config.json`:
```json
{
  "mcpServers": {
    "mq-mcp": {
      "type": "stdio",
      "command": "/bin/bash",
      "tools": [
        "*"
      ],
      "args": [
        "/home/andrew/Games/mq-mcp/start-server.sh"
      ]
    }
  }
}
```

alternatively, you can run the CLI and `/mcp` and follow the steps to adding the CLI from within there.

## Linux Installation

Everything is pretty much the same as on Windows. The main thing you have to do differently is set the WINEPREFIX that you are using in `start-server.sh` and set the pipename to `127.0.0.1:29999` in `config.json` instead of `\\.\pipe\mqpipe`. 

If your computer is refusing to run `mqpipe_bridge.exe` through wine, for whatever reason, feel free to compile it yourself. Here's how:

```C
/*
 * mqpipe_bridge.c
 * Runs under Wine. Connects to \\.\pipe\mqpipe and proxies it to a
 * TCP socket on 127.0.0.1:29999 so native Linux code can connect.
 *
 * Build:
 *   x86_64-w64-mingw32-gcc mqpipe_bridge.c -o mqpipe_bridge.exe -lws2_32
 * Run:
 *   wine mqpipe_bridge.exe &
 */
 ```

 If you get any errors, it's probably because you don't have mingw installed. Simply `sudo apt install mingw-w64` and try again.

 Do note that I wasn't able to get the TLO reference tool working, but Claude & Copilot CLI can still connect to your characters, make them chase each other, and even debug your Lua scripts in real time.

## Available Tools

| Tool | Description |
|---|---|
| `mq_eval(code)` | Execute any Lua code in the MQ environment and return the result |
| `get_tlo_reference` | Full runtime TLO type map enriched with mq-definitions docs |
| `refresh_tlo_types` | Re-run TLO introspection (use after loading/unloading plugins) |
| `list_scripts` | List all `.lua` files in your MQ lua directory |
| `read_script` | Read a Lua script by name |
| `write_script` | Write a Lua script directly to your MQ lua directory |
| `get_config` | Check current configuration status (paths, mq-definitions, MQ connection) |
| `download_mq_definitions` | Clone mq-definitions from GitHub and update config.json |

### mq_eval examples

```lua
-- Query any TLO value (no 'return' needed for single expressions)
mq.TLO.Me.Name()
mq.TLO.Me.Level()
mq.TLO.Me.Sitting()

-- Build a table of results
return { name=mq.TLO.Me.Name(), class=mq.TLO.Me.Class.Name(), level=mq.TLO.Me.Level() }

-- Run a command
mq.cmd('/sit')

-- Do something then read the result
mq.cmd('/target npc')
return { name=mq.TLO.Target.Name(), distance=mq.TLO.Target.Distance() }

-- Iterate over spell gems
local gems = {}
for i = 1, 13 do
    local s = mq.TLO.Me.Gem(i)()
    if s then gems[i] = s end
end
return gems
```
