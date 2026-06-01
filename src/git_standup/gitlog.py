"""Git log analysis — extract commits, diffs, and stats from a git repository."""

import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def get_repo_root() -> str:
    """Get the root directory of the current git repository.

    Raises:
        RuntimeError: if not in a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "Not in a git repository (or git is not installed)"
        ) from exc


def get_commits(
    days: int = 7,
    author: str | None = None,
    base_branch: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch commits for the last N days.

    Returns a list of commit dicts with keys:
        hash, author_name, author_email, date, message, files, insertions, deletions.
    """
    repo_root = get_repo_root()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

    # Build the git log command
    fmt = (
        "---COMMIT---%n"
        "hash:%H%n"
        "author:%an%n"
        "email:%ae%n"
        "date:%aI%n"
        "subject:%s%n"
        "body:%b"
    )

    cmd = [
        "git",
        "-C",
        repo_root,
        "log",
        f"--since={since}",
        f"--pretty=format:{fmt}",
        "--numstat",
    ]

    if base_branch:
        cmd.extend([f"{base_branch}..HEAD"])

    if author:
        if author == "me":
            # Get current user's name and email
            author = _get_current_user()
        cmd.extend([f"--author={author}"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git log failed: {exc.stderr}") from exc

    return _parse_log_output(result.stdout)


def _get_current_user() -> str:
    """Get the current git user (name or email)."""
    try:
        name = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        return name
    except subprocess.CalledProcessError:
        return ""


def _parse_log_output(raw: str) -> list[dict[str, Any]]:
    """Parse git log --numstat output into structured commit dicts."""
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_files: list[dict[str, Any]] = []

    for line in raw.splitlines():
        if line == "---COMMIT---":
            if current is not None:
                current["files"] = _aggregate_files(current_files)
                commits.append(current)
            current = {}
            current_files = []
        elif current is not None:
            if line.startswith("hash:"):
                current["hash"] = line[5:].strip()
            elif line.startswith("author:"):
                current["author_name"] = line[7:].strip()
            elif line.startswith("email:"):
                current["author_email"] = line[6:].strip()
            elif line.startswith("date:"):
                current["date"] = line[5:].strip()
            elif line.startswith("subject:"):
                current["subject"] = line[8:].strip()
            elif line.startswith("body:"):
                current["body"] = line[5:].strip()
            elif re.match(r"^\d+\s+\d+\s+\S", line):
                # numstat line: insertions deletions filepath
                parts = line.split("\t")
                if len(parts) == 3:
                    insertions = 0 if parts[0] == "-" else int(parts[0])
                    deletions = 0 if parts[1] == "-" else int(parts[1])
                    current_files.append(
                        {
                            "path": parts[2] if parts[2] != "-" else "unknown",
                            "insertions": insertions,
                            "deletions": deletions,
                        }
                    )

    # Don't forget the last commit
    if current is not None:
        current["files"] = _aggregate_files(current_files)
        commits.append(current)

    return commits


def _aggregate_files(
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate file stats by path (in case git shows the same file multiple times)."""
    by_path: dict[str, dict[str, Any]] = {}
    for f in files:
        path = f["path"]
        if path in by_path:
            by_path[path]["insertions"] += f["insertions"]
            by_path[path]["deletions"] += f["deletions"]
        else:
            by_path[path] = dict(f)
    return list(by_path.values())


def group_by_date(
    commits: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group commits by date (YYYY-MM-DD)."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in commits:
        dt = c.get("date", "")
        date_key = dt[:10] if dt else "unknown"
        groups[date_key].append(c)
    return dict(sorted(groups.items(), reverse=True))


def group_by_author(
    commits: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group commits by author name."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in commits:
        author = c.get("author_name", "Unknown")
        groups[author].append(c)
    return dict(groups)


def compute_stats(
    commits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate stats for a list of commits."""
    total_insertions = 0
    total_deletions = 0
    files_changed: set[str] = set()

    for c in commits:
        for f in c.get("files", []):
            total_insertions += f.get("insertions", 0)
            total_deletions += f.get("deletions", 0)
            files_changed.add(f.get("path", ""))

    return {
        "total_commits": len(commits),
        "total_insertions": total_insertions,
        "total_deletions": total_deletions,
        "total_files": len(files_changed),
        "files_changed": sorted(files_changed),
    }
