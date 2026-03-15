--- mq-mcp.lua
--- MacroQuest Actor mailbox for the MQ MCP scripting assistant.
--- Run with: /lua run mq-mcp
---
--- Registers the mailbox 'mq-mcp' and handles RPC requests from the
--- Python MCP server. Responds with JSON-encoded game state.

local mq         = require('mq')
local actors     = require('actors')
local PackageMan = require('mq/PackageMan')
local json       = PackageMan.Require('lua-cjson', 'cjson')

local MAILBOX = 'mq-mcp'

-- Cache for TLO type introspection — expensive to rebuild
local _tlo_cache = nil

local function log(msg)
    print(string.format('[mq-mcp] %s', msg))
end

-- ------------------------------------------------------------------
-- Request handlers
-- ------------------------------------------------------------------

local handlers = {}

handlers['get_character'] = function(_req)
    local gems = {}
    for i = 1, 13 do
        local spell = mq.TLO.Me.Gem(i)
        if spell() then
            gems[tostring(i)] = spell.Name()
        end
    end
    return {
        name  = mq.TLO.Me.Name(),
        class = mq.TLO.Me.Class.Name(),
        level = mq.TLO.Me.Level(),
        gems  = gems,
    }
end

handlers['get_spell_book'] = function(_req)
    local spells = {}
    local i = 1
    while true do
        local spell = mq.TLO.Me.Book(i)
        if not spell() then break end
        table.insert(spells, {
            id    = spell.ID(),
            name  = spell.Name(),
            level = spell.Level(),
        })
        i = i + 1
    end
    return { spells = spells }
end

handlers['get_plugins'] = function(_req)
    local plugins = {}
    local i = 1
    while true do
        local plugin = mq.TLO.Plugin(i)
        if not plugin() then break end
        table.insert(plugins, plugin.Name())
        i = i + 1
    end
    return { plugins = plugins }
end

local function build_tlo_types(max_scan)
    max_scan = max_scan or 2000
    log(string.format('Building TLO type map (max_scan=%d)...', max_scan))

    local type_names = mq.GetDataTypeNames()
    local types = {}

    for _, type_name in ipairs(type_names) do
        local t = mq.TLO.Type(type_name)
        if t() then
            local members = {}
            -- Member IDs are non-contiguous, so we scan the full range
            for i = 1, max_scan do
                local member_name = t.Member(i)()
                if member_name and member_name ~= '' then
                    table.insert(members, member_name)
                end
            end

            -- TODO: verify InheritedType API — may be .InheritedType() or .BaseType()
            local parent = t.InheritedType and t.InheritedType() or nil
            types[type_name] = {
                members  = members,
                inherits = (parent and parent ~= '') and parent or nil,
            }
        end
    end

    log(string.format('TLO type map built: %d types', #type_names))
    return types
end

handlers['get_tlo_types'] = function(req)
    if not _tlo_cache then
        _tlo_cache = build_tlo_types(req.max_scan)
    end
    return { types = _tlo_cache }
end

handlers['refresh_tlo_types'] = function(req)
    _tlo_cache = build_tlo_types(req.max_scan)
    return { status = 'ok' }
end

handlers['list_scripts'] = function(_req)
    -- mq.TLO.MacroQuest.Path('lua') returns the MQ lua directory
    local lua_path = mq.TLO.MacroQuest.Path('lua')()
    if not lua_path then
        return { error = 'Could not determine lua path' }
    end

    -- Use io.popen with dir to list .lua files recursively
    -- TODO: verify this works in MQ's Lua environment; lfs would be cleaner if available
    local files = {}
    local cmd   = string.format('dir /b /s "%s\\*.lua" 2>nul', lua_path)
    local pipe  = io.popen(cmd)
    if pipe then
        for line in pipe:lines() do
            -- Return paths relative to lua_path
            local rel = line:gsub(lua_path:gsub('\\', '\\\\') .. '\\', '')
            table.insert(files, rel)
        end
        pipe:close()
    end
    return { files = files }
end

handlers['read_script'] = function(req)
    local lua_path = mq.TLO.MacroQuest.Path('lua')()
    if not lua_path then
        return { error = 'Could not determine lua path' }
    end
    local path = lua_path .. '\\' .. req.name
    local f    = io.open(path, 'r')
    if not f then
        return { error = string.format('File not found: %s', req.name) }
    end
    local content = f:read('*a')
    f:close()
    return { content = content }
end

handlers['write_script'] = function(req)
    local lua_path = mq.TLO.MacroQuest.Path('lua')()
    if not lua_path then
        return { error = 'Could not determine lua path' }
    end
    local path = lua_path .. '\\' .. req.name
    local f    = io.open(path, 'w')
    if not f then
        return { error = string.format('Could not open for writing: %s', req.name) }
    end
    f:write(req.content)
    f:close()
    return { success = true, path = path }
end

-- ------------------------------------------------------------------
-- Actor message dispatch
-- ------------------------------------------------------------------

-- TODO: verify the exact MQ actors reply API.
-- Candidates based on MQ source/docs:
--   message:send(content)        -- reply to sender
--   actors.send(message.address, content)
-- The handler below uses message:send() — update if the API differs.

local function on_message(message)
    local ok, request = pcall(json.decode, message.content)
    if not ok then
        message:send(json.encode({ error = 'Invalid JSON in request' }))
        return
    end

    local msg_type = request and request.type
    local handler  = msg_type and handlers[msg_type]

    local response
    if handler then
        local ok2, result = pcall(handler, request)
        if ok2 then
            response = result
        else
            response = { error = tostring(result) }
            log(string.format('Handler error [%s]: %s', msg_type, tostring(result)))
        end
    else
        response = { error = string.format('Unknown request type: %s', tostring(msg_type)) }
    end

    message:send(json.encode(response))
end

-- ------------------------------------------------------------------
-- Startup
-- ------------------------------------------------------------------

actors.register(MAILBOX, on_message)
log(string.format("Registered Actor mailbox '%s'", MAILBOX))
log("Ready. Waiting for requests from mcp-server.")

-- Keep the script alive
while true do
    mq.delay(1000)
end
