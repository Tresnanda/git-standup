import subprocess

from git_standup.gitlog import (
    _parse_log_output,
    compute_stats,
    get_commits,
    group_by_author,
    group_by_date,
)


def test_parse_log_output_extracts_commits_and_file_stats() -> None:
    raw = """---COMMIT---
hash:abc123
author:Alice
email:alice@example.com
date:2026-03-10T09:15:00+00:00
subject:Add authentication
body:Initial implementation
12	2	src/auth.py
4	0	tests/test_auth.py
---COMMIT---
hash:def456
author:Bob
email:bob@example.com
date:2026-03-09T12:00:00+00:00
subject:Fix payment retry
body:
3	1	src/payments.py
"""

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
    raw = """---COMMIT---
hash:abc123
author:Alice
email:alice@example.com
date:2026-03-10T09:15:00+00:00
subject:Add reporting
body:First paragraph
Second paragraph
3	1	src/report.py
"""

    commits = _parse_log_output(raw)

    assert commits[0]["body"] == "First paragraph\nSecond paragraph"


def test_parse_log_output_keeps_binary_numstat_entries() -> None:
    raw = """---COMMIT---
hash:abc123
author:Alice
email:alice@example.com
date:2026-03-10T09:15:00+00:00
subject:Add demo image
body:
-	-	assets/demo.png
"""

    commits = _parse_log_output(raw)

    assert commits[0]["files"] == [
        {"path": "assets/demo.png", "insertions": 0, "deletions": 0}
    ]


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
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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
    assert "--since=2026-01-01" in calls[1]
    assert "--until=2026-01-07" in calls[1]
    assert "--no-merges" not in calls[1]
    assert calls[1][calls[1].index("-n") + 1] == "11"


def test_get_commits_can_exclude_merge_commits(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    commits = get_commits(repo_path="/workspace/app", exclude_merges=True)

    assert commits == []
    assert "--no-merges" in calls[1]
