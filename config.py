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

_CONFIG_PATH = Path(__file__).parent / "config.json"


class Config:
    def __init__(self, data: dict, config_path: Path = _CONFIG_PATH):
        self._data      = {**_DEFAULTS, **data}
        self._config_path = config_path

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def mq_lua_path(self) -> Path:
        return Path(self._data["mq_lua_path"])

    @property
    def mq_definitions_path(self) -> Optional[Path]:
        raw = self._data.get("mq_definitions_path")
        if raw:
            p = Path(raw)
            if p.exists():
                return p
        return None

    def set_mq_definitions_path(self, path: Path):
        """Update mq_definitions_path in memory and persist to config.json."""
        self._data["mq_definitions_path"] = str(path)
        with self._config_path.open() as f:
            raw = json.load(f)
        raw["mq_definitions_path"] = str(path)
        with self._config_path.open("w") as f:
            json.dump(raw, f, indent=4)

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
    config_path = path or _CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            "Copy config.json.example to config.json and fill in your paths."
        )
    with config_path.open() as f:
        data = json.load(f)
    return Config(data, config_path)
