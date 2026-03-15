--- mq-mcp.lua
--- MacroQuest Actor mailbox for the MQ MCP scripting assistant.
--- Run with: /lua run mq-mcp

local mq     = require('mq')
local actors = require('actors')

local MAILBOX = 'mq-mcp'

-- Environment for eval'd code: exposes mq/actors locals plus all globals.
-- Built lazily so it's available after all requires are done.
local _eval_env = setmetatable({ mq = mq, actors = actors }, { __index = _G })

-- Cache for TLO type introspection — expensive to rebuild
local _tlo_cache = nil

local function log(msg)
    print(string.format('[mq-mcp] %s', msg))
end

-- ------------------------------------------------------------------
-- Serialization helper
-- Recursively converts Lua values to plain tables/scalars safe for
-- the MQ Variant proto. MQ TLO userdata objects are called () to
-- extract their value.
-- ------------------------------------------------------------------

local function to_serializable(v, _depth)
    _depth = _depth or 0
    if _depth > 20 then return '<max depth>' end
    local t = type(v)
    if t == 'nil'     then return nil
    elseif t == 'boolean' then return v
    elseif t == 'number'  then return v
    elseif t == 'string'  then return v
    elseif t == 'table'   then
        local out = {}
        for k, val in pairs(v) do
            out[tostring(k)] = to_serializable(val, _depth + 1)
        end
        return out
    elseif t == 'userdata' then
        -- MQ TLO type — call () to unwrap to a plain value
        local ok, val = pcall(function() return v() end)
        if ok then return to_serializable(val, _depth + 1) end
        return tostring(v)
    else
        return tostring(v)
    end
end

-- ------------------------------------------------------------------
-- Request handlers
-- ------------------------------------------------------------------

local handlers = {}

-- eval: execute arbitrary Lua code in the MQ environment.
-- Tries to compile as an expression (implicit return) first;
-- falls back to a statement block so both forms work:
--   "mq.TLO.Me.Name()"
--   "local t = {} for i=1,3 do t[i]=i end return t"
handlers['eval'] = function(req)
    local code = req.code
    if type(code) ~= 'string' or code == '' then
        return { error = 'Missing or empty code parameter' }
    end

    -- Try expression form first
    local fn, err = load('return ' .. code)
    if not fn then
        -- Try statement block
        fn, err = load(code)
    end
    if not fn then
        return { error = 'Compile error: ' .. tostring(err) }
    end

    setfenv(fn, _eval_env)
    local results = { pcall(fn) }
    if not results[1] then
        return { error = 'Runtime error: ' .. tostring(results[2]) }
    end

    local n = #results - 1  -- number of return values (subtract the ok bool)
    if n == 0 then
        return { result = nil }
    elseif n == 1 then
        return { result = to_serializable(results[2]) }
    else
        -- Multiple return values → array
        local arr = {}
        for i = 2, #results do
            table.insert(arr, to_serializable(results[i]))
        end
        return { result = arr }
    end
end

-- get_tlo_types / refresh_tlo_types: kept as dedicated handlers because
-- they involve a long scan loop and maintain a cache.

local function build_tlo_types(max_scan)
    max_scan = max_scan or 2000
    log(string.format('Building TLO type map (max_scan=%d)...', max_scan))

    local type_names = mq.GetDataTypeNames()
    local types = {}

    for _, type_name in ipairs(type_names) do
        local t = mq.TLO.Type(type_name)
        if t() then
            local members = {}
            for i = 1, max_scan do
                local member_name = t.Member(i)()
                if member_name and member_name ~= '' then
                    table.insert(members, member_name)
                end
            end
            local parent = t.InheritedType and t.InheritedType() or nil
            types[type_name] = {
                members  = members,
                inherits = (parent and parent ~= '') and parent or nil,
            }
        end
        mq.delay()  -- yield between types so the game loop stays responsive
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

-- ------------------------------------------------------------------
-- Actor message dispatch
-- ------------------------------------------------------------------

local function on_message(message)
    local content = message.content
    log(string.format('Received message, content type: %s', type(content)))

    local request
    if type(content) == 'table' then
        request = content
    end

    if type(request) ~= 'table' then
        message:reply(1, { error = 'Expected table request, got ' .. type(content) })
        return
    end

    local msg_type = request.type
    local handler  = msg_type and handlers[msg_type]

    local response
    if handler then
        local ok, result = pcall(handler, request)
        if ok then
            response = result
        else
            response = { error = tostring(result) }
            log(string.format('Handler error [%s]: %s', msg_type, tostring(result)))
        end
    else
        response = { error = string.format('Unknown request type: %s', tostring(msg_type)) }
    end

    message:reply(0, response)
end

-- ------------------------------------------------------------------
-- Startup
-- ------------------------------------------------------------------

actors.register(MAILBOX, on_message)
log(string.format("Registered Actor mailbox '%s'", MAILBOX))

mq.delay(500)
local ok, err = pcall(function()
    actors.send({ name = 'mcp-server', absolute_mailbox = true }, { type = 'announce', mailbox = MAILBOX })
end)
if ok then
    log("Sent announce to mcp-server")
else
    log(string.format("announce failed: %s", tostring(err)))
end

log("Ready. Waiting for requests from mcp-server.")

while true do
    mq.delay(1000)
end
