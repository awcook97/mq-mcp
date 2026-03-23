--- mq-mcp.lua
--- MacroQuest Actor mailbox for the MQ MCP scripting assistant.
--- Run with: /lua run mq-mcp

local mq     = require('mq')
local actors = require('actors')

local MAILBOX = 'mq-mcp'

-- Environment for eval'd code: exposes mq/actors locals plus all globals.
local _eval_env = setmetatable({ mq = mq, actors = actors }, { __index = _G })

-- Eval queue — callbacks only enqueue; execution happens in the main loop.
-- Each entry: { message = <actor message>, code = <string> }
local _eval_queue = {}

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
    if t == 'nil'         then return nil
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
        local ok, val = pcall(function() return v() end)
        if ok then return to_serializable(val, _depth + 1) end
        return tostring(v)
    else
        return tostring(v)
    end
end

-- ------------------------------------------------------------------
-- Eval execution (runs in main loop, not in callback)
-- ------------------------------------------------------------------

local function run_eval(code)
    -- Try expression form first, fall back to statement block
    local fn, err = load('return ' .. code)
    if not fn then
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

    local n = #results - 1
    if n == 0 then
        return { result = nil }
    elseif n == 1 then
        return { result = to_serializable(results[2]) }
    else
        local arr = {}
        for i = 2, #results do
            table.insert(arr, to_serializable(results[i]))
        end
        return { result = arr }
    end
end

-- ------------------------------------------------------------------
-- Actor message dispatch (callback — must return quickly, no Lua eval)
-- ------------------------------------------------------------------

local function on_message(message)
    local content = message.content
    if type(content) ~= 'table' then
        message:reply(1, { error = 'Expected table request, got ' .. type(content) })
        return
    end

    local msg_type = content.type

    if msg_type == 'eval' then
        -- Enqueue for main loop execution — never run user code in a callback
        local code = content.code
        if type(code) ~= 'string' or code == '' then
            message:reply(1, { error = 'Missing or empty code parameter' })
        else
            table.insert(_eval_queue, { message = message, code = code })
        end
        -- No reply yet — reply happens in main loop after execution

    else
        -- Unknown request
        message:reply(0, { error = 'Unknown request type: ' .. tostring(msg_type) })
    end
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

-- ------------------------------------------------------------------
-- Main loop — process eval queue here, where all Lua is safe to run
-- ------------------------------------------------------------------

while true do
    -- Drain the eval queue one item per loop tick
    if #_eval_queue > 0 then
        local item = table.remove(_eval_queue, 1)
        local ok, response = pcall(run_eval, item.code)
        if ok then
            item.message:reply(0, response)
        else
            item.message:reply(0, { error = tostring(response) })
        end
    end

    mq.delay(100)
end
