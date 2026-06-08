"""User preference config for git-standup."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

APP_NAME = "git-standup"
_ASSIGNMENT_RE = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*|\"(?:\\.|[^\"])*\")\s*=\s*(?P<value>.+?)\s*$"
)
_SECTION_RE = re.compile(r"^\[([A-Za-z_][A-Za-z0-9_-]*)\]\s*$")
_SECRET_KEYS = {"api_key", "apikey", "key", "token", "secret", "password"}
_ALLOWED_KEYS = {"provider", "base_url", "model", "harness", "api_key_env"}


@dataclass(frozen=True)
class AIConfig:
    provider: str = ""
    base_url: str = ""
    model: str = ""
    harness: str = ""
    api_key_env: str = ""
    author_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)


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


def _parse_key(raw_key: str) -> str:
    if raw_key.startswith('"') and raw_key.endswith('"'):
        return _unescape(raw_key[1:-1])
    return raw_key


def _parse_string_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not (value.startswith('"') and value.endswith('"')):
        raise ValueError(f"Expected string value: {raw_value}")
    return _unescape(value[1:-1])


def _parse_string_list(raw_value: str) -> tuple[str, ...]:
    value = raw_value.strip()
    if value.startswith('"'):
        return tuple(
            item.strip()
            for item in re.split(r"[,|]", _parse_string_value(value))
            if item.strip()
        )
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(f"Expected string or string list value: {raw_value}")
    inner = value[1:-1].strip()
    if not inner:
        return ()
    items: list[str] = []
    index = 0
    while index < len(inner):
        while index < len(inner) and inner[index].isspace():
            index += 1
        if index >= len(inner) or inner[index] != '"':
            raise ValueError(f"Expected string list value: {raw_value}")
        index += 1
        chars: list[str] = []
        escaped = False
        terminated = False
        while index < len(inner):
            char = inner[index]
            index += 1
            if escaped:
                chars.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                terminated = True
                break
            else:
                chars.append(char)
        if not terminated:
            raise ValueError(f"Unterminated string list value: {raw_value}")
        items.append(_unescape("".join(chars)))
        while index < len(inner) and inner[index].isspace():
            index += 1
        if index >= len(inner):
            break
        if inner[index] != ",":
            raise ValueError(f"Expected comma in string list value: {raw_value}")
        index += 1
    return tuple(item for item in items if item.strip())


def parse_config_text(text: str) -> AIConfig:
    """Parse the small TOML subset used for git-standup preferences."""
    data: dict[str, str] = {}
    author_aliases: dict[str, tuple[str, ...]] = {}
    section = ""
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        section_match = _SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1)
            if section != "author_aliases":
                raise ValueError(f"Unsupported config section: {section}")
            continue
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            raise ValueError(f"Invalid config line {line_number}: {raw_line}")
        key = _parse_key(match.group("key"))
        value = match.group("value")
        if section == "author_aliases":
            aliases = _parse_string_list(value)
            if not key or not aliases:
                raise ValueError("Author alias entries need a name and at least one alias.")
            author_aliases[key] = aliases
            continue
        if _is_secret_key(key):
            raise ValueError("Secrets do not belong in the git-standup config file.")
        if key not in _ALLOWED_KEYS:
            raise ValueError(f"Unsupported config field: {key}")
        data[key] = _parse_string_value(value)
    return AIConfig(
        provider=data.get("provider", ""),
        base_url=data.get("base_url", ""),
        model=data.get("model", ""),
        harness=data.get("harness", ""),
        api_key_env=data.get("api_key_env", ""),
        author_aliases=author_aliases,
    )


def format_config(config: AIConfig) -> str:
    """Format preferences as a compact TOML file."""
    lines = [
        "# git-standup AI defaults. Store API keys in environment variables, not here.",
    ]
    for key in ("provider", "base_url", "model", "harness", "api_key_env"):
        value = getattr(config, key)
        if value:
            lines.append(f'{key} = "{_escape(value)}"')
    if config.author_aliases:
        if len(lines) > 1:
            lines.append("")
        lines.append("[author_aliases]")
        for canonical, aliases in config.author_aliases.items():
            alias_values = ", ".join(f'"{_escape(alias)}"' for alias in aliases)
            lines.append(f'"{_escape(canonical)}" = [{alias_values}]')
    return "\n".join(lines) + "\n"


def load_config(path: Path | None = None) -> AIConfig | None:
    """Load user preferences, returning None when no config exists."""
    config_file = path or config_path()
    if not config_file.exists():
        return None
    return parse_config_text(config_file.read_text(encoding="utf-8"))


def save_config(path: Path | None, config: AIConfig) -> Path:
    """Persist user preferences and return the written path."""
    config_file = path or config_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(format_config(config), encoding="utf-8")
    return config_file


def reset_config(path: Path | None = None) -> bool:
    """Remove user preferences. Return True when a file was deleted."""
    config_file = path or config_path()
    if not config_file.exists():
        return False
    config_file.unlink()
    return True
