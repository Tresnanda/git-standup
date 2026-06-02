"""Persist an environment variable for future shells — no dependencies.

On Unix this appends an ``export`` line to the user's shell profile; on Windows
it runs ``setx``. Mirrors the behavior of the install scripts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping


def _shell_profile(env: Mapping[str, str], home: Path) -> Path:
    """Pick the shell profile to write based on $SHELL, preferring existing files."""
    shell = env.get("SHELL", "")
    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("bash"):
        return home / ".bashrc"
    for name in (".zshrc", ".bashrc", ".profile"):
        candidate = home / name
        if candidate.exists():
            return candidate
    return home / ".profile"


def _shell_quote(value: str) -> str:
    """Single-quote a value safely for a POSIX shell."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def persist_env_var(
    name: str,
    value: str,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str | None:
    """Persist ``name=value`` for future shells.

    Returns a human-readable description of where it was written (a profile path
    or ``"setx"``), or None on failure.
    """
    environment = os.environ if env is None else env

    if os.name == "nt":
        try:
            subprocess.run(["setx", name, value], check=True, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return None
        return "setx"

    profile = _shell_profile(environment, home or Path.home())
    export_line = f"export {name}={_shell_quote(value)}"

    try:
        existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
        kept = [
            line
            for line in existing.splitlines()
            if not line.strip().startswith(f"export {name}=")
        ]
        kept.append(export_line)
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("\n".join(kept).strip() + "\n", encoding="utf-8")
    except OSError:
        return None
    return str(profile)
