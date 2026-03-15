"""MQ MCP server — exposes MacroQuest context to Claude via MCP."""

import asyncio
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from config import load_config
from mq_actor import MQActorClient, MQNotConnectedError

log = logging.getLogger(__name__)

cfg    = load_config()
actor  = MQActorClient(cfg.pipe_name, cfg.actor_name, cfg.actor_mailbox)
mcp    = FastMCP("mq-mcp")


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------

async def _connect_actor():
    try:
        actor.connect()
    except Exception as e:
        log.warning("Could not connect to MQ pipe: %s — tools will fail until MQ is running", e)


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
# Game state tools (via Actor RPC)
# ------------------------------------------------------------------

@mcp.tool()
async def get_character(character_name: str = "") -> dict:
    """Get character info from MQ — name, class, level, spell gems.

    Args:
        character_name: Specific character to query. Omit for the active character.
    """
    msg = {"type": "get_character"}
    if character_name:
        msg["character"] = character_name
    return await actor.call(cfg.lua_mailbox, msg, timeout=cfg.rpc_timeout)


@mcp.tool()
async def get_spell_book(character_name: str = "") -> dict:
    """Get the full spell book for a character.

    Args:
        character_name: Specific character to query. Omit for the active character.
    """
    msg = {"type": "get_spell_book"}
    if character_name:
        msg["character"] = character_name
    return await actor.call(cfg.lua_mailbox, msg, timeout=cfg.rpc_timeout)


@mcp.tool()
async def get_plugins() -> dict:
    """Get the list of currently loaded MQ plugins."""
    return await actor.call(cfg.lua_mailbox, {"type": "get_plugins"}, timeout=cfg.rpc_timeout)


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
    asyncio.run(_start())


async def _start():
    await _connect_actor()
    await mcp.run_async()


if __name__ == "__main__":
    main()
