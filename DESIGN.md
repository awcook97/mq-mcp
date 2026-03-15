# MQ MCP — MacroQuest Scripting Assistant

## Overview

An MCP (Model Context Protocol) server that gives AI assistants (Claude) accurate, live context about a running MacroQuest instance. The primary use case is **Lua scripting assistance** — generating correct, immediately-runnable Lua scripts by grounding Claude in real game state and the actual MQ TLO/type system.

---

## Goals

- Claude can write MQ Lua scripts that run correctly on the first try
- Claude has accurate knowledge of available TLOs, types, and members (no hallucination)
- Claude knows the character's class, level, spell gems, and loaded plugins before writing scripts
- Claude can read existing scripts for style/context and write new ones directly to disk
- Live, on-demand data via RPC — not stale file snapshots

## Non-Goals

- `.mac` macro support (Lua only)
- Live gameplay automation or combat assistance
- Remote/network access (localhost only)

---

## Architecture

```
Claude (MCP Client)
    ↕  MCP protocol (stdio)
Python MCP Server
    ↕  Named pipe \\.\pipe\mqpipe + protobuf (Actor RPC)
MQ Actor System
    ↕  Actor RPC (CallAndResponse)
Lua Actor Client (mq-mcp.lua, running in-game)
    ↕  mq.TLO.*
EverQuest Game State
```

### Components

| Component | Language | Role |
|---|---|---|
| `server.py` | Python | MCP server, Actor RPC client, mq-definitions loader |
| `mq-mcp.lua` | Lua | In-game Actor mailbox, TLO query handler |
| `mq-definitions/` | LuaCATS | Static type reference (bundled + local override) |
| `config.json` | JSON | User-specific paths and settings |

---

## Communication: MQ Actor RPC

MacroQuest's Actor system provides named pipe IPC with full RPC (request/response) support.

**Transport**: `\\.\pipe\mqpipe` (Windows named pipe, localhost only)

**Wire format**: 16-byte `MQMessageHeader` + Protocol Buffers payload

**Message modes**:
- `SimpleMessage` — fire and forget
- `CallAndResponse` — RPC request, expects reply
- `MessageReply` — response back to caller

**Python Actor address**:
```
name:    "mcp-server"
mailbox: "scripting-assistant"
```

**Lua Actor address**:
```
mailbox: "mq-mcp"
```

### RPC Message Types

| Request | Lua Handler Returns |
|---|---|
| `get_character` | name, class, level, spell gems |
| `get_spells` | full spell book |
| `get_plugins` | loaded plugin names |
| `get_tlo_types` | runtime type map (all types + members) |
| `list_scripts` | filenames in lua/ dir |
| `read_script` | file contents |
| `write_script` | writes file, returns success |

---

## TLO Reference System

Claude needs accurate TLO/type/member information to write correct code. Two layers:

### Layer 1: Runtime Introspection (ground truth)

The Lua client enumerates all registered types and members via the `Type` TLO:

```lua
-- Get all type names (includes plugin-registered types)
local typeNames = mq.GetDataTypeNames()

-- For each type, enumerate all members
-- Member IDs are non-contiguous, iterate to MAX to find all
local MAX_MEMBER_SCAN = 2000
for i = 1, MAX_MEMBER_SCAN do
    local memberName = mq.TLO.Type(typeName).Member(i)()
    if memberName and memberName ~= "" then
        table.insert(members, memberName)
    end
end

-- Walk inheritance chain
local parent = mq.TLO.Type(typeName).InheritedType()
```

This produces a live type map reflecting the exact MQ version and loaded plugins.

### Layer 2: mq-definitions Enrichment (descriptions + signatures)

The `mq-definitions` repository contains LuaCATS-annotated Lua files with:
- Parameter types and names
- Return types
- Member descriptions
- Usage examples

Cross-referencing runtime data with mq-definitions gives Claude both accuracy (runtime) and richness (docs).

**Sources (in priority order)**:
1. Local path from config (user's VS Code extension copy — always fresh)
2. Bundled copy in MCP server directory (fallback, updated via `git clone`)

---

## mq-definitions

**Repository**: https://github.com/macroquest/mq-definitions

**Structure**:
```
mq-definitions/
  mq/tlo/          Core TLOs (_Me.lua, _Target.lua, _Spawn.lua, ...)
  mq/plugins/      Plugin TLOs (Cast/, DanNet/, Melee/, ...)
  mq/datatype/     Type definitions
  mq/enum/         Enumerations
```

**Config**:
```json
{
  "mq_definitions_path": "C:\\Users\\...\\mq-definitions-master"
}
```
If `null`, falls back to bundled copy at `./mq-definitions/`.

---

## Configuration

`config.json` (lives alongside `server.py`):

```json
{
  "mq_lua_path": "C:\\Users\\kingj\\AppData\\Local\\VeryVanilla\\Emu\\Release\\lua",
  "mq_definitions_path": "C:\\Users\\kingj\\AppData\\Roaming\\Code\\User\\globalStorage\\zenithcodeforge.mq-defs\\file-downloader-downloads\\macroquest\\mq-definitions-master",
  "pipe_name": "\\\\.\\pipe\\mqpipe",
  "actor_name": "mcp-server",
  "actor_mailbox": "scripting-assistant",
  "max_member_scan": 2000
}
```

All paths are user-configurable. `mq_definitions_path: null` uses bundled copy.

---

## MCP Tools Exposed to Claude

### Script Tools
| Tool | Description |
|---|---|
| `list_scripts` | List all `.lua` files in `mq_lua_path` |
| `read_script(name)` | Read a Lua script by filename |
| `write_script(name, content)` | Write a Lua script to `mq_lua_path` |

### Game State Tools (via Actor RPC)
| Tool | Description |
|---|---|
| `get_character()` | Name, class, level, spell gems for active character |
| `get_spell_book()` | All known spells with IDs and names |
| `get_plugins()` | Currently loaded MQ plugins |
| `get_tlo_reference()` | Full runtime type map enriched with mq-definitions docs |

### Utility Tools
| Tool | Description |
|---|---|
| `refresh_tlo_types()` | Re-run TLO introspection (after plugin load/unload) |

---

## Multi-Character Support

Each EQ client running MQ sends state tagged with character name. The MCP server tracks multiple characters and Claude can query a specific one:

```
get_character(name="Soandso")   # specific character
get_character()                  # active/foreground character
```

---

## Project Structure

```
mq-mcp/
  server.py              MCP server entrypoint
  mq_actor.py            Named pipe + protobuf Actor RPC client
  mq_definitions.py      mq-definitions LuaCATS parser
  config.py              Config loader
  tools/
    scripts.py           list/read/write script tools
    game_state.py        character/spells/plugins tools
    tlo_reference.py     TLO type reference tool
  proto/                 MQ protobuf schema files
  mq-definitions/        Bundled mq-definitions clone
  lua/
    mq-mcp.lua           In-game Actor client script
  config.json            User configuration
  pyproject.toml         Python project definition
  README.md
```

---

## Dependencies

**Python**:
- `mcp` — MCP server SDK
- `protobuf` — Protocol Buffers
- `pywin32` — Windows named pipe access

**Lua** (all available in MQ):
- `mq` — MacroQuest API
- `actors` — MQ Actor system

---

## Setup

1. Clone this repo
2. `pip install -e .`
3. Copy `config.json.example` to `config.json` and set paths
4. Copy `lua/mq-mcp.lua` to your MQ `lua/` directory
5. In-game: `/lua run mq-mcp`
6. Add MCP server to Claude config:
   ```json
   {
     "mcpServers": {
       "macroquest": {
         "command": "python",
         "args": ["path/to/mq-mcp/server.py"]
       }
     }
   }
   ```

---

## Open Questions

- [ ] Locate MQ protobuf `.proto` schema files in the MQ source repo
- [ ] Confirm exact Actor address format Python must use to register
- [ ] Determine if `mq.GetDataTypeNames()` includes plugin-registered types or only core types
- [ ] Decide whether `write_script` should go through Actor RPC (Lua writes) or direct filesystem (Python writes)
