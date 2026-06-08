"""Since-last-standup checkpoint persistence.

The checkpoint file is non-secret user data. It stores the last successful report
start time per repository so future runs can opt into the same window without
remembering dates.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple

APP_NAME = "git-standup"
CHECKPOINT_VERSION = 1


class CheckpointUpdate(NamedTuple):
    """One repository checkpoint update."""

    repository_id: str
    since: str
    label: str = ""


def data_home(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the user data directory base without creating it."""
    environment = os.environ if env is None else env
    if os.name == "nt" and environment.get("LOCALAPPDATA"):
        return Path(environment["LOCALAPPDATA"])
    if environment.get("XDG_DATA_HOME"):
        return Path(environment["XDG_DATA_HOME"])
    return (home or Path.home()) / ".local" / "share"


def checkpoint_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the since-last checkpoint file path without creating it."""
    return data_home(env=env, home=home) / APP_NAME / "checkpoints.json"


def local_repository_id(repo_root: str) -> str:
    """Return the stable checkpoint key for a local repository root."""
    return f"local:{str(Path(repo_root).resolve())}"


def remote_repository_id(repo_label: str) -> str:
    """Return the stable checkpoint key for a remote repository label."""
    return f"remote:{repo_label}"


def empty_checkpoint_data() -> dict[str, Any]:
    """Return an empty checkpoint document."""
    return {"version": CHECKPOINT_VERSION, "repositories": {}}


def load_checkpoints(path: Path | None = None) -> dict[str, Any]:
    """Load checkpoint data, returning an empty document when no file exists."""
    checkpoint_file = path or checkpoint_path()
    if not checkpoint_file.exists():
        return empty_checkpoint_data()
    try:
        loaded = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid checkpoint file: {checkpoint_file}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid checkpoint file: {checkpoint_file}")
    repositories = loaded.get("repositories")
    if repositories is None:
        repositories = {}
        loaded["repositories"] = repositories
    if not isinstance(repositories, dict):
        raise ValueError(f"Invalid checkpoint file: {checkpoint_file}")
    loaded.setdefault("version", CHECKPOINT_VERSION)
    return loaded


def checkpoint_since(data: Mapping[str, Any], repository_id: str) -> str | None:
    """Return the stored timestamp for a repository, if present and valid."""
    repositories = data.get("repositories")
    if not isinstance(repositories, Mapping):
        return None
    entry = repositories.get(repository_id)
    if not isinstance(entry, Mapping):
        return None
    since = entry.get("since")
    return since if isinstance(since, str) and since else None


def save_checkpoints(data: Mapping[str, Any], path: Path | None = None) -> Path:
    """Persist checkpoint data atomically and return the written path."""
    checkpoint_file = path or checkpoint_path()
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    temporary = checkpoint_file.with_name(f".{checkpoint_file.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(checkpoint_file)
    return checkpoint_file


def update_checkpoints(
    updates: Iterable[CheckpointUpdate],
    path: Path | None = None,
) -> Path:
    """Apply repository checkpoint updates and return the written path."""
    checkpoint_file = path or checkpoint_path()
    data = load_checkpoints(checkpoint_file)
    repositories = data.setdefault("repositories", {})
    if not isinstance(repositories, dict):
        raise ValueError(f"Invalid checkpoint file: {checkpoint_file}")
    for update in updates:
        entry: dict[str, str] = {"since": update.since}
        if update.label:
            entry["label"] = update.label
        repositories[update.repository_id] = entry
    data["version"] = CHECKPOINT_VERSION
    return save_checkpoints(data, checkpoint_file)
