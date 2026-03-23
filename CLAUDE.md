# MQ MCP — Claude Context

## What This Is

An MCP (Model Context Protocol) server that connects Claude to a live MacroQuest (EverQuest) game instance. Primary use case: helping Claude write correct Lua scripts for MQ by giving it real game state and the actual type system.

## Architecture

```
Claude (MCP Client)
    ↕  MCP (stdio)
server.py  — Python MCP server
    ↕  Named pipe \\.\pipe\mqpipe + protobuf (Actor RPC)
mq-mcp.lua — In-game Lua Actor mailbox
    ↕  mq.TLO.*
EverQuest game state
```

## Key Files

| File | Purpose |
|---|---|
| `server.py` | MCP server + all tool definitions |
| `mq_actor.py` | Named pipe Actor RPC client (Python) |
| `config.py` | Loads `config.json` |
| `lua/mq-mcp.lua` | In-game Lua script — register with `/lua run mq-mcp` |
| `config.json` | User paths (gitignored) |
| `config.json.example` | Template |
| `DESIGN.md` | Architecture reference |
| `TASKS.md` | Phase-by-phase task list |
| `proto/` | MQ protobuf schemas + generated Python stubs |

## MCP Tools

- `get_config()` — check connection/path status
- `list_characters()` — connected EQ clients
- `mq_eval(code, character?)` — execute arbitrary Lua in-game
- `get_tlo_reference(character?)` — full runtime type map (cached)
- `refresh_tlo_types(character?)` — re-scan after plugin changes
- `list_scripts()` / `read_script(name)` / `write_script(name, content)` — Lua file I/O
- `download_mq_definitions()` — git clone mq-definitions

## mq-definitions

LuaCATS definitions for VS Code IntelliSense. Format:
```lua
---@class TypeName : ParentType
---@field public MemberName ReturnType Description
```

**Local path** (from VS Code extension `zenithcodeforge.mq-defs`):
```
C:\Users\kingj\AppData\Roaming\Code\User\globalStorage\zenithcodeforge.mq-defs\
  file-downloader-downloads\macroquest\mq-definitions-master\
```

**Structure**:
```
mq/datatype/   type definitions (one file per type, e.g. _character.lua)
mq/tlo/        top-level object bindings (_Achievement.lua, etc.)
mq/plugins/    plugin-specific TLOs (DanNet/, Cast/, etc.)
mq/enum/       enumerations
mq/mq.lua      core mq module definitions
```

## Config (config.json)

```json
{
  "mq_lua_path": "C:\\Users\\kingj\\AppData\\Local\\VeryVanilla\\Emu\\Release\\lua",
  "mq_definitions_path": "C:\\Users\\kingj\\AppData\\Roaming\\Code\\User\\globalStorage\\zenithcodeforge.mq-defs\\file-downloader-downloads\\macroquest\\mq-definitions-master",
  "pipe_name": "\\\\.\\pipe\\mqpipe",
  "actor_name": "mcp-server",
  "actor_mailbox": "scripting-assistant",
  "lua_mailbox": "lua:mq-mcp:mq-mcp",
  "max_member_scan": 2000,
  "rpc_timeout": 10.0
}
```

## Running

```bash
pip install -e .
# In-game: /lua run mq-mcp
python server.py
```

## TLO Type Introspection

Runtime type map built via:
```lua
mq.GetDataTypeNames()                    -- all registered type names
mq.TLO.Type(typeName).Member(i)()        -- member name at index i
mq.TLO.Type(typeName).InheritedType()    -- parent type
```

Member IDs are sparse; Lua scans up to `max_member_scan` (default 2000) with early-stop after 50 consecutive empty slots. Scan is async — triggered by `start_tlo_scan` message, result returned via `tlo_complete` announce.
