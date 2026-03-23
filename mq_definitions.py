"""Parser for mq-definitions LuaCATS annotation files.

Reads ---@class and ---@field annotations from the mq-definitions repo
and builds an in-memory index of documented types and their members.
"""

import re
from pathlib import Path
from typing import Optional

# Matches: ---@class TypeName or ---@class TypeName : ParentType
_CLASS_RE = re.compile(r"^---@class\s+(\S+?)(?:\s*:\s*(\S+))?(?:\s|$)")
# Matches: ---@field [public|protected|private] MemberName ...
_FIELD_RE = re.compile(r"^---@field\s+(?:public\s+|protected\s+|private\s+)?(\S+)")


def _parse_file(path: Path) -> dict[str, set[str]]:
    """Return {type_name_lower: {member_name_lower, ...}} from one .lua file."""
    result: dict[str, set[str]] = {}
    current_class: Optional[str] = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _CLASS_RE.match(line)
        if m:
            current_class = m.group(1).lower()
            if current_class not in result:
                result[current_class] = set()
            continue

        m = _FIELD_RE.match(line)
        if m and current_class is not None:
            result[current_class].add(m.group(1).lower())

    return result


def parse_definitions(defs_path: Path) -> dict[str, set[str]]:
    """Parse MQ TLO datatype LuaCATS files under defs_path.

    Only scans mq/datatype/ and mq/plugins/ — skipping imgui/, zep/, mq/tlo/,
    and top-level mq/*.lua files which contain Lua bindings and TLO objects
    rather than MQ datatype definitions.

    Returns a dict mapping lower-case type name → set of lower-case member names.
    """
    scan_dirs = [
        defs_path / "mq" / "datatype",
    ]
    combined: dict[str, set[str]] = {}
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for lua_file in scan_dir.rglob("*.lua"):
            for type_name, members in _parse_file(lua_file).items():
                if type_name in combined:
                    combined[type_name].update(members)
                else:
                    combined[type_name] = set(members)
    return combined


def diff_against_runtime(
    runtime_types: dict,
    defs_path: Optional[Path],
) -> dict:
    """Compare runtime type map against mq-definitions.

    Args:
        runtime_types: The ``types`` dict from a ``tlo_complete`` payload —
            {type_name: {members: [str, ...], inherits: str|None}}
        defs_path: Root of an mq-definitions checkout. If None, defs are
            treated as empty (everything will be flagged as undocumented).

    Returns a dict with:
        undocumented_types   — types in runtime but no @class in defs
        undocumented_members — {type_name: [member, ...]} members in runtime
                               but no @field in defs (for types that ARE documented)
        stale_members        — {type_name: [member, ...]} @field in defs but
                               not in runtime (possible stale/wrong definitions)
        summary              — quick counts
    """
    defs = parse_definitions(defs_path) if defs_path else {}

    undocumented_types: list[str] = []
    undocumented_members: dict[str, list[str]] = {}
    stale_members: dict[str, list[str]] = {}

    for type_name, type_info in runtime_types.items():
        key = type_name.lower()
        runtime_member_set = {m.lower() for m in (type_info.get("members") or [])}

        if key not in defs:
            undocumented_types.append(type_name)
            continue

        def_member_set = defs[key]

        missing = sorted(runtime_member_set - def_member_set)
        if missing:
            undocumented_members[type_name] = missing

        extra = sorted(def_member_set - runtime_member_set)
        if extra:
            stale_members[type_name] = extra

    return {
        "undocumented_types": sorted(undocumented_types),
        "undocumented_members": undocumented_members,
        "stale_members": stale_members,
        "summary": {
            "runtime_type_count": len(runtime_types),
            "documented_type_count": len(defs),
            "undocumented_type_count": len(undocumented_types),
            "types_with_missing_members": len(undocumented_members),
            "types_with_stale_members": len(stale_members),
        },
    }
