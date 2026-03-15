"""Configuration loader for mq-mcp."""

import json
from pathlib import Path
from typing import Optional

_DEFAULTS = {
    "pipe_name": r"\\.\pipe\mqpipe",
    "actor_name": "mcp-server",
    "actor_mailbox": "scripting-assistant",
    "lua_mailbox": "lua:mq-mcp:mq-mcp",
    "max_member_scan": 2000,
    "rpc_timeout": 10.0,
}

_BUNDLED_MQ_DEFS = Path(__file__).parent / "mq-definitions"


class Config:
    def __init__(self, data: dict):
        self._data = {**_DEFAULTS, **data}

    @property
    def mq_lua_path(self) -> Path:
        return Path(self._data["mq_lua_path"])

    @property
    def mq_definitions_path(self) -> Path:
        raw = self._data.get("mq_definitions_path")
        if raw:
            p = Path(raw)
            if p.exists():
                return p
        if _BUNDLED_MQ_DEFS.exists():
            return _BUNDLED_MQ_DEFS
        raise FileNotFoundError(
            "mq-definitions not found. Set mq_definitions_path in config.json "
            "or run: git clone https://github.com/macroquest/mq-definitions"
        )

    @property
    def pipe_name(self) -> str:
        return self._data["pipe_name"]

    @property
    def actor_name(self) -> str:
        return self._data["actor_name"]

    @property
    def actor_mailbox(self) -> str:
        return self._data["actor_mailbox"]

    @property
    def lua_mailbox(self) -> str:
        return self._data["lua_mailbox"]

    @property
    def max_member_scan(self) -> int:
        return int(self._data["max_member_scan"])

    @property
    def rpc_timeout(self) -> float:
        return float(self._data["rpc_timeout"])


def load_config(path: Optional[Path] = None) -> Config:
    config_path = path or Path(__file__).parent / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            "Copy config.json.example to config.json and fill in your paths."
        )
    with config_path.open() as f:
        data = json.load(f)
    return Config(data)
