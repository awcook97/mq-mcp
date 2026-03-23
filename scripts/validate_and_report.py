"""validate_and_report.py

Connects to MQ as a separate actor, queries each type in mq-definitions one at
a time via mq_eval, and writes a markdown report per type to validation/.

Usage:
    python scripts/validate_and_report.py
"""

import asyncio
import sys
import time
from pathlib import Path

# Make sure we can import from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from mq_actor import MQActorClient
from mq_definitions import parse_definitions

_GET_MEMBERS_LUA = """\
local t = mq.TLO.Type({type_name!r})
if not t() then return nil end
local members = {{}}
local empty_run = 0
for i = 1, 200 do
    local m = t.Member(i)()
    if m and m ~= '' then
        table.insert(members, m)
        empty_run = 0
    else
        empty_run = empty_run + 1
        if empty_run >= 15 then break end
    end
end
local parent = t.InheritedType and t.InheritedType() or nil
return {{ members = members, inherits = (parent and parent ~= '') and parent or nil }}
"""

_DELAY_BETWEEN_TYPES = 1.0   # seconds — conservative to avoid crashing MQ

OUT_DIR = Path(__file__).parent.parent / "validation"


def write_report(type_name: str, runtime_members: set[str], def_members: set[str],
                 inherits: str | None):
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{type_name}.md"

    missing = sorted(runtime_members - def_members)   # in game, not in defs
    stale   = sorted(def_members - runtime_members)   # in defs, not in game
    matched = sorted(runtime_members & def_members)

    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {type_name}\n\n")
        if inherits:
            f.write(f"**Inherits:** `{inherits}`\n\n")
        f.write(f"| | Count |\n|---|---|\n")
        f.write(f"| Runtime members | {len(runtime_members)} |\n")
        f.write(f"| Documented members | {len(def_members)} |\n")
        f.write(f"| Matched | {len(matched)} |\n")
        f.write(f"| Missing from defs | {len(missing)} |\n")
        f.write(f"| Stale in defs | {len(stale)} |\n\n")

        if missing:
            f.write("## Missing from mq-definitions\n\n")
            f.write("These members exist at runtime but have no `@field` in the definitions.\n\n")
            for m in missing:
                f.write(f"- `{m}`\n")
            f.write("\n")

        if stale:
            f.write("## Stale in mq-definitions\n\n")
            f.write("These `@field` entries exist in the definitions but were not found at runtime.\n\n")
            for m in stale:
                f.write(f"- `{m}`\n")
            f.write("\n")

        if matched:
            f.write("## Matched\n\n")
            f.write("<details><summary>Show all matched members</summary>\n\n")
            for m in matched:
                f.write(f"- `{m}`\n")
            f.write("\n</details>\n")


async def main():
    cfg = load_config()

    if not cfg.mq_definitions_path:
        print("ERROR: mq_definitions_path not set in config.json")
        sys.exit(1)

    print(f"Parsing mq-definitions from {cfg.mq_definitions_path} ...")
    defs = parse_definitions(cfg.mq_definitions_path)
    print(f"  {len(defs)} types found in definitions")

    # Connect as a separate actor so we don't conflict with the MCP server
    actor = MQActorClient(cfg.pipe_name, "mcp-validator", "validator")
    actor.connect()
    await asyncio.sleep(0.5)

    clients = actor.list_clients()
    if not clients:
        print("ERROR: No EQ clients found — is mq-mcp.lua running?")
        sys.exit(1)

    char = clients[0]["character"]
    print(f"  Connected, targeting character: {char}\n")

    OUT_DIR.mkdir(exist_ok=True)
    total = len(defs)
    no_runtime = []

    for i, (type_name, def_members) in enumerate(sorted(defs.items()), 1):
        print(f"[{i:3}/{total}] {type_name} ...", end=" ", flush=True)

        code = _GET_MEMBERS_LUA.format(type_name=type_name)
        try:
            result = await actor.call(
                cfg.lua_mailbox,
                {"type": "eval", "code": code},
                timeout=cfg.rpc_timeout,
            )
        except Exception as e:
            print(f"ERROR: {e}")
            no_runtime.append(type_name)
            await asyncio.sleep(_DELAY_BETWEEN_TYPES)
            continue

        payload = result.get("result") if isinstance(result, dict) else None

        if payload is None:
            print("not found at runtime")
            no_runtime.append(type_name)
            # Write a stub report so we still have a file
            write_report(type_name, set(), def_members, None)
            await asyncio.sleep(_DELAY_BETWEEN_TYPES)
            continue

        # result is {members: {1: "Name", 2: ...}, inherits: "..."}
        raw_members = payload.get("members") or {}
        runtime_members = {v.lower() for v in raw_members.values() if isinstance(v, str)}
        inherits = payload.get("inherits")

        missing = len(runtime_members - def_members)
        stale   = len(def_members - runtime_members)
        print(f"{len(runtime_members)} runtime members  |  {missing} missing from defs  |  {stale} stale in defs")

        write_report(type_name, runtime_members, def_members, inherits)
        await asyncio.sleep(_DELAY_BETWEEN_TYPES)

    print(f"\nDone. Reports written to {OUT_DIR}/")
    print(f"Types not found at runtime: {len(no_runtime)}")
    if no_runtime:
        for t in sorted(no_runtime):
            print(f"  - {t}")

    actor.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
