import subprocess
from pathlib import Path

from git_standup.gitlog import (
    _parse_log_output,
    compute_stats,
    describe_commit_quality,
    get_commits,
    group_by_author,
    group_by_date,
)


def _nul_log_record(
    commit_hash: str,
    author: str,
    email: str,
    date: str,
    subject: str,
    body: str,
    *numstat_tokens: bytes,
) -> bytes:
    fields = [
        b"\x1e" + commit_hash.encode(),
        author.encode(),
        email.encode(),
        date.encode(),
        subject.encode(),
        body.encode(),
    ]
    raw = b"\x00".join(fields) + b"\x00"
    if numstat_tokens:
        raw += b"\n" + b"\x00".join(numstat_tokens) + b"\x00"
    return raw


def test_parse_log_output_extracts_commits_and_file_stats() -> None:
    raw = _nul_log_record(
        "abc123",
        "Alice",
        "alice@example.com",
        "2026-03-10T09:15:00+00:00",
        "Add authentication",
        "Initial implementation",
        b"12	2	src/auth.py",
        b"4	0	tests/test_auth.py",
    ) + _nul_log_record(
        "def456",
        "Bob",
        "bob@example.com",
        "2026-03-09T12:00:00+00:00",
        "Fix payment retry",
        "",
        b"3	1	src/payments.py",
    )

    commits = _parse_log_output(raw)

    assert commits == [
        {
            "hash": "abc123",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T09:15:00+00:00",
            "subject": "Add authentication",
            "body": "Initial implementation",
            "files": [
                {"path": "src/auth.py", "insertions": 12, "deletions": 2},
                {"path": "tests/test_auth.py", "insertions": 4, "deletions": 0},
            ],
        },
        {
            "hash": "def456",
            "author_name": "Bob",
            "author_email": "bob@example.com",
            "date": "2026-03-09T12:00:00+00:00",
            "subject": "Fix payment retry",
            "body": "",
            "files": [{"path": "src/payments.py", "insertions": 3, "deletions": 1}],
        },
    ]


def test_parse_log_output_preserves_multiline_commit_body() -> None:
    raw = _nul_log_record(
        "abc123",
        "Alice",
        "alice@example.com",
        "2026-03-10T09:15:00+00:00",
        "Add reporting",
        "First paragraph\nSecond paragraph",
        b"3	1	src/report.py",
    )

    commits = _parse_log_output(raw)

    assert commits[0]["body"] == "First paragraph\nSecond paragraph"


def test_parse_log_output_keeps_binary_numstat_entries() -> None:
    raw = _nul_log_record(
        "abc123",
        "Alice",
        "alice@example.com",
        "2026-03-10T09:15:00+00:00",
        "Add demo image",
        "",
        b"-	-	assets/demo.png",
    )

    commits = _parse_log_output(raw)

    assert commits[0]["files"] == [
        {"path": "assets/demo.png", "insertions": 0, "deletions": 0}
    ]


def test_parse_log_output_handles_nul_rename_path_tokens() -> None:
    raw = _nul_log_record(
        "abc123",
        "Alice",
        "alice@example.com",
        "2026-03-10T09:15:00+00:00",
        "Rename file",
        "",
        b"0\t0\t",
        "old\tname.txt".encode(),
        "new\nname.txt".encode(),
    )

    commits = _parse_log_output(raw)

    assert commits[0]["files"] == [
        {"path": "old\tname.txt => new\nname.txt", "insertions": 0, "deletions": 0}
    ]


def test_get_commits_handles_nul_delimited_body_and_paths(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Alice"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "alice@example.com"], cwd=tmp_path, check=True
    )
    nested = tmp_path / "docs"
    nested.mkdir()
    weird_path = nested / "name\nwith\ttabs.txt"
    weird_path.write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    body = "Line-delimited parser poison\n---COMMIT---\n1\t2\tfake.py\nbody:fake"
    subprocess.run(
        ["git", "commit", "-q", "-m", "Add weird path", "-m", body],
        cwd=tmp_path,
        check=True,
    )

    commits = get_commits(repo_path=str(tmp_path), since="1970-01-01", max_commits=1)

    assert len(commits) == 1
    assert commits[0]["subject"] == "Add weird path"
    assert commits[0]["body"] == body
    assert commits[0]["files"] == [
        {"path": "docs/name\nwith\ttabs.txt", "insertions": 1, "deletions": 0}
    ]


def test_describe_commit_quality_flags_generic_subjects() -> None:
    quality = describe_commit_quality({"subject": "wip", "body": ""})

    assert quality == {
        "signal": "low",
        "reasons": ["generic subject `wip`", "no commit body to clarify intent"],
        "guidance": "Summarize only concrete file evidence; do not embellish this commit.",
    }


def test_describe_commit_quality_leaves_specific_subjects_unmarked() -> None:
    assert describe_commit_quality({"subject": "Fix payment retry", "body": ""}) is None


def test_grouping_and_stats_are_stable() -> None:
    commits = [
        {
            "author_name": "Alice",
            "date": "2026-03-10T09:15:00+00:00",
            "files": [{"path": "src/app.py", "insertions": 5, "deletions": 1}],
        },
        {
            "author_name": "Alice",
            "date": "2026-03-10T11:00:00+00:00",
            "files": [{"path": "src/app.py", "insertions": 2, "deletions": 0}],
        },
        {
            "author_name": "Bob",
            "date": "2026-03-09T12:00:00+00:00",
            "files": [{"path": "README.md", "insertions": 3, "deletions": 0}],
        },
    ]

    assert list(group_by_author(commits)) == ["Alice", "Bob"]
    assert list(group_by_date(commits)) == ["2026-03-10", "2026-03-09"]
    assert compute_stats(commits) == {
        "total_commits": 3,
        "total_insertions": 10,
        "total_deletions": 1,
        "total_files": 2,
        "files_changed": ["README.md", "src/app.py"],
    }


def test_get_commits_uses_repo_path_and_explicit_date_window(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    commits = get_commits(
        repo_path="/workspace/app",
        since="2026-01-01",
        until="2026-01-07",
        max_commits=11,
    )

    assert commits == []
    assert calls[0] == ["git", "-C", "/workspace/app", "rev-parse", "--show-toplevel"]
    assert calls[1][:4] == ["git", "-C", "/workspace/app", "log"]
    assert "-z" in calls[1]
    assert "--since=2026-01-01" in calls[1]
    assert "--until=2026-01-07" in calls[1]
    assert "--no-merges" not in calls[1]
    assert calls[1][calls[1].index("-n") + 1] == "11"


def test_get_commits_appends_pathspecs_after_separator(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    commits = get_commits(
        repo_path="/workspace/app",
        base_branch="main",
        author="Alice",
        since="2026-01-01",
        exclude_merges=True,
        pathspecs=["src", "tests/test_cli.py"],
    )

    assert commits == []
    log_cmd = calls[1]
    assert log_cmd[:4] == ["git", "-C", "/workspace/app", "log"]
    assert "-z" in log_cmd
    assert "main..HEAD" in log_cmd
    assert "--author=Alice" in log_cmd
    assert "--no-merges" in log_cmd
    separator_index = log_cmd.index("--")
    assert log_cmd[separator_index + 1 :] == ["src", "tests/test_cli.py"]


def test_get_commits_fetches_multiple_authors_separately(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        author_arg = next((part for part in cmd if part.startswith("--author=")), "")
        if author_arg == "--author=Kevin":
            stdout = _nul_log_record(
                "abc123",
                "Kevin",
                "kevin@example.com",
                "2026-03-10T09:15:00+00:00",
                "Add Kevin work",
                "",
                b"1	0	src/kevin.py",
            )
        elif author_arg == "--author=YusufRehan":
            stdout = _nul_log_record(
                "def456",
                "YusufRehan",
                "yusuf@example.com",
                "2026-03-10T10:15:00+00:00",
                "Add Yusuf work",
                "",
                b"2	0	src/yusuf.py",
            )
        else:
            stdout = b""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    commits = get_commits(repo_path="/workspace/app", author="Kevin|YusufRehan")

    log_calls = [cmd for cmd in calls if "log" in cmd]
    assert ["--author=Kevin"] == [
        part for part in log_calls[0] if part.startswith("--author=")
    ]
    assert ["--author=YusufRehan"] == [
        part for part in log_calls[1] if part.startswith("--author=")
    ]
    assert [commit["author_name"] for commit in commits] == ["YusufRehan", "Kevin"]
