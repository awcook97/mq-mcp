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


async def _build_tlo_cache(character: str = "") -> dict:
    """Fetch TLO types via RPC, cache by UUID, and return the result."""
    client = actor.get_client(character)
    result = await actor.call(
        cfg.lua_mailbox,
        {"type": "get_tlo_types", "max_scan": cfg.max_member_scan},
        timeout=60.0,
        character=character,
    )
    if client:
        _tlo_cache[client["uuid"]] = result
        log.info("TLO cache built for %s (%d types)",
                 client["character"], len(result.get("types") or {}))
    return result


async def _warm_tlo_cache():
    """Background task: build TLO cache for all connected characters at startup."""
    await asyncio.sleep(3)   # let MQ identity responses settle
    for client in actor.list_clients():
        if client["uuid"] not in _tlo_cache:
            try:
                await _build_tlo_cache(client["character"])
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
    return await _build_tlo_cache(character)


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
    # Tell Lua to invalidate its cache too, then rebuild
    await actor.call(
        cfg.lua_mailbox,
        {"type": "refresh_tlo_types", "max_scan": cfg.max_member_scan},
        timeout=60.0,
        character=character,
    )
    await _build_tlo_cache(character)
    return "ok"


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO)
    try:
        actor.connect()
    except Exception as e:
        log.warning("Could not connect to MQ pipe: %s — game state tools will fail until MQ is running", e)

    async def _run():
        asyncio.create_task(_warm_tlo_cache())
        await mcp.run_stdio_async()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
