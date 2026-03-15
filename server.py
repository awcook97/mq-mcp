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
    }


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
async def mq_eval(code: str) -> dict:
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
        code: Lua code to execute. Single expressions or full statement blocks.
    """
    return await actor.call(cfg.lua_mailbox, {"type": "eval", "code": code}, timeout=cfg.rpc_timeout)


@mcp.tool()
async def get_tlo_reference() -> dict:
    """Get the full TLO type reference — all types, members, and inheritance.

    Combines runtime introspection from MQ with mq-definitions documentation.
    Results are cached in MQ until refresh_tlo_types() is called.
    """
    return await actor.call(
        cfg.lua_mailbox,
        {"type": "get_tlo_types", "max_scan": cfg.max_member_scan},
        timeout=60.0,  # type introspection can take a moment
    )


@mcp.tool()
async def refresh_tlo_types() -> str:
    """Force MQ to re-run TLO type introspection.

    Useful after loading or unloading plugins that register new types.
    """
    result = await actor.call(
        cfg.lua_mailbox,
        {"type": "refresh_tlo_types", "max_scan": cfg.max_member_scan},
        timeout=60.0,
    )
    return result.get("status", "done")


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO)
    try:
        actor.connect()
    except Exception as e:
        log.warning("Could not connect to MQ pipe: %s — game state tools will fail until MQ is running", e)
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
