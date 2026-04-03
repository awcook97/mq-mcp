"""MQ MCP server — exposes MacroQuest context to Claude via MCP."""

import asyncio
import logging
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from config import load_config
from mq_actor import MQActorClient, MQNotConnectedError

log = logging.getLogger(__name__)

cfg    = load_config()
actor  = MQActorClient(cfg.pipe_name, cfg.actor_name, cfg.actor_mailbox)
mcp    = FastMCP("mq-mcp")

# TLO cache keyed by character UUID — populated at startup and on demand.
_tlo_cache: dict[str, dict] = {}

# One asyncio.Event per UUID — set when tlo_complete announce arrives.
_tlo_scan_events: dict[str, asyncio.Event] = {}


def _on_incoming_message(msg: dict, from_uuid: str):
    """Handle push messages from Lua (called on the asyncio event loop)."""
    _dbg(f"incoming msg: {msg!r} from_uuid={from_uuid!r}")
    if not isinstance(msg, dict):
        return
    if msg.get("type") == "announce":
        # Lua script (re)started — register the character from the announce payload.
        # The announce includes the character name; combined with from_uuid we can
        # register without relying on the IDENTIFICATION broadcast mechanism.
        character = msg.get("character") or ""
        _dbg(f"announce: character={character!r} from_uuid={from_uuid!r}")
        if from_uuid and character:
            actor.register_client(from_uuid, character)
        else:
            # Fallback: request identities via IDENTIFICATION broadcast
            _dbg("announce: missing uuid or character, falling back to refresh_clients")
            actor.refresh_clients()
    elif msg.get("type") == "tlo_complete":
        _tlo_cache[from_uuid] = {"types": msg.get("types")}
        log.info("TLO scan complete from uuid=%r (%d types)",
                 from_uuid, len((msg.get("types") or {})))
        ev = _tlo_scan_events.get(from_uuid)
        if ev:
            ev.set()


# ------------------------------------------------------------------
# Setup / config tools
# ------------------------------------------------------------------

_MQ_DEFS_REPO = "https://github.com/macroquest/mq-definitions"
_MQ_DEFS_DEFAULT = Path(__file__).parent / "mq-definitions"


@mcp.tool()
async def get_config() -> dict:
    """Return the current mq-mcp configuration status.

    Use this to check what is and isn't set up before attempting other tools.
    """
    defs_path = cfg.mq_definitions_path
    return {
        "mq_lua_path":          str(cfg.mq_lua_path),
        "mq_lua_path_exists":   cfg.mq_lua_path.exists(),
        "mq_definitions_path":  str(defs_path) if defs_path else None,
        "mq_definitions_ready": defs_path is not None,
        "mq_connected":         actor.is_connected(),
        "connected_characters": [c["character"] for c in actor.list_clients()],
        "tlo_cache_ready":      [c["character"] for c in actor.list_clients()
                                 if c["uuid"] in _tlo_cache],
    }


@mcp.tool()
async def list_characters() -> list[dict]:
    """List all EQ characters currently connected to MacroQuest.

    Returns account, server, and character name for each connected client.
    Each character must have mq-mcp.lua running for mq_eval to work on them.
    """
    return actor.list_clients()


@mcp.tool()
async def refresh_characters() -> list[dict]:
    """Re-request character identities from MQ and return the updated list.

    Use this after the game crashes and restarts, or after a new EQ client
    connects. The server caches identities at startup; this forces a refresh.
    """
    actor.refresh_clients()
    await asyncio.sleep(0.5)  # allow identity responses to arrive
    return actor.list_clients()


@mcp.tool()
async def download_mq_definitions(install_path: str = "") -> str:
    """Clone mq-definitions from GitHub and update config.json.

    Downloads https://github.com/macroquest/mq-definitions to the given path
    (defaults to a 'mq-definitions' folder next to server.py) and saves the
    path to config.json so it is used automatically on next startup.

    Args:
        install_path: Where to clone the repo. Leave blank for the default location.
    """
    target = Path(install_path) if install_path else _MQ_DEFS_DEFAULT

    if target.exists():
        return f"mq-definitions already exists at {target} — no download needed."

    try:
        subprocess.run(
            ["git", "clone", _MQ_DEFS_REPO, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git clone failed: {e.stderr.strip()}") from e
    except FileNotFoundError:
        raise RuntimeError("git not found — please install Git and try again.")

    cfg.set_mq_definitions_path(target)
    return f"Downloaded mq-definitions to {target} and updated config.json."


# ------------------------------------------------------------------
# Script tools
# ------------------------------------------------------------------

@mcp.tool()
async def list_scripts() -> list[str]:
    """List all Lua scripts in the MQ lua directory."""
    lua_dir = cfg.mq_lua_path
    if not lua_dir.exists():
        return []
    return sorted(p.name for p in lua_dir.rglob("*.lua"))


@mcp.tool()
async def read_script(name: str) -> str:
    """Read a Lua script from the MQ lua directory.

    Args:
        name: Filename or relative path within the lua directory (e.g. 'myscript.lua')
    """
    path = cfg.mq_lua_path / name
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {name}")
    return path.read_text(encoding="utf-8")


@mcp.tool()
async def write_script(name: str, content: str) -> str:
    """Write a Lua script to the MQ lua directory.

    Args:
        name:    Filename or relative path within the lua directory
        content: Full Lua script content
    """
    path = cfg.mq_lua_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Wrote {path}"


# ------------------------------------------------------------------
# Game state / execution tool (via Actor RPC)
# ------------------------------------------------------------------

@mcp.tool()
async def mq_eval(code: str, character: str = "") -> dict:
    """Execute Lua code in the MacroQuest environment and return the result.

    The code runs inside the in-game Lua runtime with full access to mq.TLO,
    mq.cmd(), and all loaded plugins. Use 'return' to get a value back.

    Single expressions work without 'return':
        mq.TLO.Me.Name()
        mq.TLO.Me.Level()
        mq.TLO.Target.Distance()

    Use 'return' for explicit results or multi-line code:
        return { name=mq.TLO.Me.Name(), level=mq.TLO.Me.Level() }

    Run a command (no return value needed):
        mq.cmd('/echo hello from claude')

    Args:
        code:      Lua code to execute. Single expressions or full statement blocks.
        character: Character name to target. Omit to use the first connected character.
    """
    return await actor.call(cfg.lua_mailbox, {"type": "eval", "code": code},
                            timeout=cfg.rpc_timeout, character=character)


async def _request_tlo_scan(character: str = "", force: bool = False):
    """Send a start_tlo_scan SimpleMessage and wait for the tlo_complete announce."""
    client = actor.get_client(character)
    if not client:
        raise MQNotConnectedError("No EQ client connected")
    uuid = client["uuid"]

    # If a scan is already in progress, just wait on the existing event.
    if uuid not in _tlo_scan_events:
        _tlo_scan_events[uuid] = asyncio.Event()
        actor.send(cfg.lua_mailbox,
                   {"type": "start_tlo_scan", "max_scan": cfg.max_member_scan, "force": force},
                   character=character)

    try:
        await asyncio.wait_for(_tlo_scan_events[uuid].wait(), timeout=120.0)
    except asyncio.TimeoutError:
        _tlo_scan_events.pop(uuid, None)
        raise RuntimeError(f"TLO scan timed out for {client['character']}")
    finally:
        _tlo_scan_events.pop(uuid, None)


async def _warm_tlo_cache():
    """Background task: trigger TLO scan for all connected characters at startup."""
    await asyncio.sleep(3)   # let MQ identity responses settle
    for client in actor.list_clients():
        if client["uuid"] not in _tlo_cache and client["uuid"] not in _tlo_scan_events:
            try:
                await _request_tlo_scan(client["character"])
            except Exception as e:
                log.warning("TLO warm-up failed for %s: %s", client["character"], e)


@mcp.tool()
async def get_tlo_reference(character: str = "") -> dict:
    """Get the full TLO type reference — all types, members, and inheritance.

    Combines runtime introspection from MQ with mq-definitions documentation.
    Built at server startup and cached — returns immediately on subsequent calls.
    Use refresh_tlo_types() to rebuild after loading or unloading plugins.

    Args:
        character: Character to introspect from. Omit to use the first connected character.
    """
    client = actor.get_client(character)
    if client and client["uuid"] in _tlo_cache:
        return _tlo_cache[client["uuid"]]
    await _request_tlo_scan(character)
    return _tlo_cache.get(client["uuid"] if client else "", {})


@mcp.tool()
async def refresh_tlo_types(character: str = "") -> str:
    """Rebuild the TLO type reference from scratch.

    Clears the cached reference and re-runs introspection. Use this after
    loading or unloading plugins that register new TLO types.

    Args:
        character: Character to refresh on. Omit to use the first connected character.
    """
    client = actor.get_client(character)
    if client:
        _tlo_cache.pop(client["uuid"], None)
        _tlo_scan_events.pop(client["uuid"], None)
    await _request_tlo_scan(character, force=True)
    return "ok"


# ------------------------------------------------------------------
# Definition validation tool
# ------------------------------------------------------------------

_GET_MEMBERS_LUA = """
local t = mq.TLO.Type({type_name!r})
if not t() then return nil end
local members = {{}}
local empty_run = 0
for i = 0, 500 do
    local m = t.Member(i)()
    if m and m ~= '' then
        table.insert(members, m)
        empty_run = 0
    else
        empty_run = empty_run + 1
        if empty_run >= 30 then break end
    end
end
local parent = t.InheritedType and t.InheritedType() or nil
return {{ members = members, inherits = (parent ~= '') and parent or nil }}
"""


@mcp.tool()
async def validate_definitions(character: str = "") -> dict:
    """Validate mq-definitions LuaCATS annotations against the live runtime type system.

    Queries each documented type individually via mq_eval (no big scan loop).
    For each type in mq-definitions, fetches its runtime members and compares.

    Returns:
    - undocumented_types   — types in runtime (mq.GetDataTypeNames) with no @class in defs
    - undocumented_members — {type: [members]} present at runtime but missing @field in defs
    - stale_members        — {type: [members]} in defs @field but absent at runtime
    - summary              — counts

    Args:
        character: Character to introspect from. Omit to use the first connected character.
    """
    if not cfg.mq_definitions_path:
        raise RuntimeError("mq_definitions_path not set in config.json")

    from mq_definitions import parse_definitions, diff_against_runtime

    defs = parse_definitions(cfg.mq_definitions_path)

    # Build runtime type map by querying each type individually via mq_eval.
    # Sleep between requests to avoid flooding the MQ pipe.
    runtime_types: dict = {}
    for type_name in defs:
        code = _GET_MEMBERS_LUA.format(type_name=type_name)
        try:
            result = await actor.call(
                cfg.lua_mailbox,
                {"type": "eval", "code": code},
                timeout=cfg.rpc_timeout,
                character=character,
            )
            if isinstance(result, dict) and result.get("result"):
                runtime_types[type_name] = result["result"]
        except Exception as e:
            log.warning("Failed to query type %r: %s", type_name, e)
        await asyncio.sleep(0.1)  # give MQ time to breathe between queries

    return diff_against_runtime(runtime_types, cfg.mq_definitions_path)


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

def _start_bridge():
    """Ensure the pipe bridge is running.

    If the bridge is already accepting connections, do nothing.
    Otherwise start a new wine bridge process without killing anything —
    wineserver (shared with MacroQuest) must never be killed.

    stdout/stderr are redirected to DEVNULL — the MCP protocol owns stdout,
    so any output from the bridge would corrupt the stream.
    """
    import socket
    import time

    bridge = Path(__file__).parent / "mqpipe_bridge.exe"
    if not bridge.exists():
        log.warning("mqpipe_bridge.exe not found — skipping bridge auto-start")
        return

    # Parse host:port from config
    pipe_name = cfg.pipe_name
    if ":" in pipe_name and not pipe_name.startswith("\\"):
        host, port_str = pipe_name.rsplit(":", 1)
        port = int(port_str)
    else:
        host, port = "127.0.0.1", 29999

    def _port_listening() -> bool:
        """Check if something is already bound to the bridge port WITHOUT connecting.

        Connecting would trigger the bridge to accept and block on open_pipe(),
        consuming its single connection slot before actor.connect() can use it.
        Instead, try to bind to the same port — if that fails, something owns it.
        """
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
            probe.close()
            return False  # bind succeeded → port is free → bridge not running
        except OSError:
            return True   # bind failed → port in use → bridge already running

    if _port_listening():
        log.info("Bridge already up on %s:%d", host, port)
        return

    # Bridge is down. Don't touch wineserver (MacroQuest lives there).
    # Just launch a new bridge instance; if wineserver still holds the port
    # from the previous dead bridge, the new process will exit immediately
    # and we wait for the port to free up before trying again.
    log.info("Bridge not running — starting wine mqpipe_bridge.exe")
    try:
        subprocess.Popen(
            ["wine", str(bridge)],
            cwd=str(Path(__file__).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        log.warning("wine not found — cannot auto-start bridge")
        return

    # Poll until bridge is up (up to ~5 s)
    for _ in range(10):
        time.sleep(0.5)
        if _port_listening():
            log.info("Bridge up on %s:%d", host, port)
            return

    log.warning("Bridge did not come up after 5 s on %s:%d", host, port)


def _dbg(msg):
    """Write directly to debug log — bypasses logging config entirely."""
    import os, traceback as _tb
    with open("/tmp/mq-mcp-startup.log", "a") as f:
        f.write(f"[{os.getpid()}] {msg}\n")
        f.flush()

def main():
    logging.basicConfig(level=logging.INFO)
    _dbg("main() entered")
    _start_bridge()
    _dbg("bridge done")
    try:
        actor.connect()
    except Exception as e:
        log.warning("Could not connect to MQ pipe: %s — game state tools will fail until MQ is running", e)

    async def _run():
        actor.set_message_handler(_on_incoming_message, asyncio.get_running_loop())
        # _warm_tlo_cache disabled — big scan crashes MQ; use validate_definitions instead
        # asyncio.create_task(_warm_tlo_cache())
        await mcp.run_stdio_async()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
