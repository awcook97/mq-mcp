# MQ MCP — Task List

## Phase 1: Actor Protocol Foundation

### 1.1 — Locate MQ Protobuf Schemas
Find the `.proto` files in the MacroQuest source repo that define the Actor system message format (`Envelope`, `Address`, `MQMessageHeader`). Confirm the exact 16-byte header layout.
- [ ] Locate `.proto` files in macroquest/macroquest repo
- [ ] Document `MQMessageHeader` struct layout
- [ ] Document `Envelope` and `Address` protobuf schemas
- [ ] Generate Python protobuf stubs (`protoc`)

### 1.2 — Python Named Pipe Actor Client
Implement a Python class that connects to `\\.\pipe\mqpipe` and speaks the Actor protocol.
- [ ] Connect to named pipe via `pywin32`
- [ ] Implement `MQMessageHeader` encode/decode
- [ ] Implement `SimpleMessage` send
- [ ] Implement `CallAndResponse` RPC (send + await reply by sequence ID)
- [ ] Handle reconnection on pipe disconnect
- [ ] Basic integration test (ping MQ, verify response)

### 1.3 — Lua Actor Client Script (`mq-mcp.lua`)
Write the in-game Lua script that registers as an Actor mailbox and handles RPC requests.
- [ ] Register mailbox `mq-mcp` via `actors` module
- [ ] Implement request dispatcher (route by message type)
- [ ] Handle `get_character` → return name, class, level, gems
- [ ] Handle `get_spell_book` → return known spells
- [ ] Handle `get_plugins` → return loaded plugin names
- [ ] Handle `get_tlo_types` → run type introspection, return type map
- [ ] Handle `list_scripts` → return lua dir listing
- [ ] Handle `read_script` → return file contents
- [ ] Handle `write_script` → write file, return success
- [ ] Multi-character awareness (tag responses with character name)
- [ ] `/mcp` command to start/stop/status

---

## Phase 2: TLO Reference

### 2.1 — Runtime TLO Introspection
Implement the Lua-side type map builder.
- [ ] Call `mq.GetDataTypeNames()` to get all type names
- [ ] For each type, iterate members 1–`max_member_scan` (default 2000), skip gaps
- [ ] Walk `InheritedType` chain to record inheritance
- [ ] Return structured type map via Actor RPC

### 2.2 — mq-definitions Parser
Python module that reads LuaCATS-annotated `.lua` files from mq-definitions and extracts type info.
- [ ] Parse `---@class` annotations
- [ ] Parse `---@field` annotations (name, type, description)
- [ ] Parse `---@param` and `---@return` on functions
- [ ] Handle plugin TLO files separately (keyed by plugin name)
- [ ] Build in-memory index: `{type_name: {members: [...], description: ...}}`

### 2.3 — Enriched TLO Reference
Merge runtime type map (ground truth) with mq-definitions (descriptions/signatures).
- [ ] Cross-reference runtime member list with mq-definitions entries
- [ ] Filter plugin TLO docs by loaded plugins (from `get_plugins`)
- [ ] Produce final reference structure for MCP resource
- [ ] Handle mq-definitions path: prefer config local path, fall back to bundled

### 2.4 — Bundle mq-definitions
- [ ] Add setup step: `git clone https://github.com/macroquest/mq-definitions`
- [ ] Document update process in README

---

## Phase 3: MCP Server

### 3.1 — Project Scaffold
- [ ] `pyproject.toml` with dependencies (`mcp`, `protobuf`, `pywin32`)
- [ ] `config.py` — load/validate `config.json`, apply defaults
- [ ] `config.json.example`

### 3.2 — MCP Server Entrypoint
- [ ] `server.py` — initialize MCP server (stdio transport)
- [ ] Connect Actor RPC client on startup
- [ ] Register all tools

### 3.3 — Script Tools
- [ ] `list_scripts` — scan `mq_lua_path` for `.lua` files, return names
- [ ] `read_script(name)` — read file from `mq_lua_path`
- [ ] `write_script(name, content)` — write file to `mq_lua_path`

### 3.4 — Game State Tools
- [ ] `get_character(name?)` — RPC call, return character data
- [ ] `get_spell_book(name?)` — RPC call, return spell list
- [ ] `get_plugins()` — RPC call, return plugin list
- [ ] `get_tlo_reference()` — return enriched type reference

### 3.5 — Utility Tools
- [ ] `refresh_tlo_types()` — re-run introspection via RPC

---

## Phase 4: Polish & Packaging

### 4.1 — Error Handling
- [ ] Graceful degradation if MQ not running (pipe not available)
- [ ] Timeout handling for RPC calls
- [ ] Meaningful error messages back to Claude

### 4.2 — README & Setup Docs
- [ ] Installation steps
- [ ] Claude Desktop / Claude Code config snippet
- [ ] In-game setup instructions (`/lua run mq-mcp`)
- [ ] Config reference

### 4.3 — End-to-End Testing
- [ ] Test: list scripts returns correct files
- [ ] Test: get_character returns correct class/level
- [ ] Test: get_tlo_reference includes expected types (Me, Target, Spawn)
- [ ] Test: write_script creates file on disk
- [ ] Test: plugin TLOs appear when plugin is loaded

---

## Open Questions (resolve before or during Phase 1)

- [ ] Confirm `.proto` file locations in MQ source repo
- [x] Confirm exact Actor address registration format for external (non-MQ) process — `lua:<script>:<mailbox>`
- [ ] Confirm `mq.GetDataTypeNames()` includes plugin-registered types
- [ ] Decide: `write_script` via filesystem (Python) or Actor RPC (Lua) — filesystem is simpler
