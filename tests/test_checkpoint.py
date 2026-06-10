from pathlib import Path

import pytest

from git_standup.checkpoint import (
    CheckpointUpdate,
    checkpoint_path,
    checkpoint_since,
    load_checkpoints,
    local_repository_id,
    remote_repository_id,
    update_checkpoints,
)


def test_checkpoint_path_uses_xdg_data_home(tmp_path: Path) -> None:
    path = checkpoint_path(env={"XDG_DATA_HOME": str(tmp_path)}, home=tmp_path / "home")

    assert path == tmp_path / "git-standup" / "checkpoints.json"


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.json"
    local_id = local_repository_id(str(tmp_path / "repo"))
    remote_id = remote_repository_id("owner/api")

    written = update_checkpoints(
        [
            CheckpointUpdate(local_id, "2026-06-08 09:00:00 +0000", str(tmp_path / "repo")),
            CheckpointUpdate(remote_id, "2026-06-08 10:00:00 +0000", "owner/api"),
        ],
        path,
    )

    assert written == path
    data = load_checkpoints(path)
    assert checkpoint_since(data, local_id) == "2026-06-08 09:00:00 +0000"
    assert checkpoint_since(data, remote_id) == "2026-06-08 10:00:00 +0000"
    assert data["repositories"][remote_id]["label"] == "owner/api"


def test_invalid_checkpoint_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid checkpoint file"):
        load_checkpoints(path)
