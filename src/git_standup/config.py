"""User AI preference config for git-standup."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

APP_NAME = "git-standup"
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*\"(.*)\"\s*$")
_SECRET_KEYS = {"api_key", "apikey", "key", "token", "secret", "password"}
_ALLOWED_KEYS = {"provider", "base_url", "model", "harness"}


@dataclass(frozen=True)
class AIConfig:
    provider: str = ""
    base_url: str = ""
    model: str = ""
    harness: str = ""


def config_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the user config path without creating it."""
    environment = os.environ if env is None else env
    if os.name == "nt" and environment.get("APPDATA"):
        return Path(environment["APPDATA"]) / APP_NAME / "config.toml"
    if environment.get("XDG_CONFIG_HOME"):
        return Path(environment["XDG_CONFIG_HOME"]) / APP_NAME / "config.toml"
    return (home or Path.home()) / ".config" / APP_NAME / "config.toml"


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _SECRET_KEYS
        or normalized.endswith("_key")
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
    )


def _unescape(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace('"', r"\"")


def parse_config_text(text: str) -> AIConfig:
    """Parse the small TOML subset used for AI preferences."""
    data: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            raise ValueError(f"Invalid config line {line_number}: {raw_line}")
        key, value = match.groups()
        if _is_secret_key(key):
            raise ValueError("Secrets do not belong in the git-standup config file.")
        if key not in _ALLOWED_KEYS:
            raise ValueError(f"Unsupported config field: {key}")
        data[key] = _unescape(value)
    return AIConfig(
        provider=data.get("provider", ""),
        base_url=data.get("base_url", ""),
        model=data.get("model", ""),
        harness=data.get("harness", ""),
    )


def format_config(config: AIConfig) -> str:
    """Format preferences as a compact TOML file."""
    lines = [
        "# git-standup AI defaults. Store API keys in environment variables, not here.",
    ]
    for key in ("provider", "base_url", "model", "harness"):
        value = getattr(config, key)
        if value:
            lines.append(f'{key} = "{_escape(value)}"')
    return "\n".join(lines) + "\n"


def load_config(path: Path | None = None) -> AIConfig | None:
    """Load user AI preferences, returning None when no config exists."""
    config_file = path or config_path()
    if not config_file.exists():
        return None
    return parse_config_text(config_file.read_text(encoding="utf-8"))


def save_config(path: Path | None, config: AIConfig) -> Path:
    """Persist user AI preferences and return the written path."""
    config_file = path or config_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(format_config(config), encoding="utf-8")
    return config_file


def reset_config(path: Path | None = None) -> bool:
    """Remove user AI preferences. Return True when a file was deleted."""
    config_file = path or config_path()
    if not config_file.exists():
        return False
    config_file.unlink()
    return True
