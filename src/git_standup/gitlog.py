"""Git log analysis — extract commits, diffs, and stats from a git repository."""

import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

_LOW_SIGNAL_SUBJECTS = {
    "change",
    "changes",
    "checkpoint",
    "commit",
    "fix",
    "fixed",
    "misc",
    "miscellaneous",
    "save",
    "stuff",
    "temp",
    "test",
    "testing",
    "tmp",
    "todo",
    "update",
    "updates",
    "wip",
    "work",
    "work in progress",
}

_LOW_SIGNAL_PREFIXES = {
    "change",
    "changes",
    "fix",
    "fixed",
    "misc",
    "save",
    "temp",
    "test",
    "tmp",
    "update",
    "updates",
    "wip",
}

_LOW_SIGNAL_OBJECTS = {
    "change",
    "changes",
    "code",
    "stuff",
    "things",
    "work",
}


def describe_commit_quality(commit: dict[str, Any]) -> dict[str, Any] | None:
    """Return low-signal commit-message metadata, or None for normal commits.

    The goal is not to judge whether the work was valuable. It only marks commit
    messages that give the summarizer weak evidence, so AI output can avoid
    dressing up placeholder history as polished accomplishments.
    """
    subject = str(commit.get("subject", "")).strip()
    body = str(commit.get("body", "")).strip()
    normalized = _normalize_subject(subject)
    reasons: list[str] = []

    if not subject:
        reasons.append("missing commit subject")
    elif normalized in _LOW_SIGNAL_SUBJECTS:
        reasons.append(f"generic subject `{subject}`")
    elif _is_low_signal_phrase(normalized):
        reasons.append(f"vague subject `{subject}`")

    if reasons and not body:
        reasons.append("no commit body to clarify intent")

    if not reasons:
        return None

    return {
        "signal": "low",
        "reasons": reasons,
        "guidance": "Summarize only concrete file evidence; do not embellish this commit.",
    }


def _normalize_subject(subject: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", subject.lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def _is_low_signal_phrase(normalized: str) -> bool:
    parts = normalized.split()
    if len(parts) != 2:
        return False
    first, second = parts
    return first in _LOW_SIGNAL_PREFIXES and second in _LOW_SIGNAL_OBJECTS


def get_repo_root(repo_path: str | None = None) -> str:
    """Get the root directory of the current git repository.

    Raises:
        RuntimeError: if not in a git repository.
    """
    cmd = ["git"]
    if repo_path:
        cmd.extend(["-C", repo_path])
    cmd.extend(["rev-parse", "--show-toplevel"])

    try:
        result = subprocess.run(
            cmd,
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
    repo_path: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_commits: int | None = None,
    exclude_merges: bool = False,
    pathspecs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch commits for the last N days.

    Returns a list of commit dicts with keys:
        hash, author_name, author_email, date, message, files, insertions, deletions.
    """
    repo_root = get_repo_root(repo_path)
    if author and "|" in author:
        commits_by_hash: dict[str, dict[str, Any]] = {}
        for author_part in (part.strip() for part in author.split("|")):
            if not author_part:
                continue
            for commit in get_commits(
                days=days,
                author=author_part,
                base_branch=base_branch,
                repo_path=repo_root,
                since=since,
                until=until,
                max_commits=max_commits,
                exclude_merges=exclude_merges,
                pathspecs=pathspecs,
            ):
                commit_hash = str(commit.get("hash", ""))
                if commit_hash:
                    commits_by_hash[commit_hash] = commit
        return sorted(
            commits_by_hash.values(),
            key=lambda commit: str(commit.get("date", "")),
            reverse=True,
        )

    since_arg = since or (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
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
        f"--since={since_arg}",
        f"--pretty=format:{fmt}",
        "--numstat",
    ]

    if until:
        cmd.insert(5, f"--until={until}")

    if exclude_merges:
        cmd.append("--no-merges")

    if max_commits is not None:
        cmd.extend(["-n", str(max_commits)])

    if base_branch:
        cmd.extend([f"{base_branch}..HEAD"])

    if author:
        if author == "me":
            # Get current user's name and email
            author = _get_current_user(repo_root)
        cmd.extend([f"--author={author}"])

    if pathspecs:
        cmd.append("--")
        cmd.extend(pathspecs)

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


def _get_current_user(repo_root: str | None = None) -> str:
    """Get the current git user (name or email)."""
    cmd = ["git"]
    if repo_root:
        cmd.extend(["-C", repo_root])
    cmd.extend(["config", "user.name"])
    try:
        name = subprocess.run(
            cmd,
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
    reading_body = False
    body_lines: list[str] = []

    for line in raw.splitlines():
        if line == "---COMMIT---":
            if current is not None:
                current["body"] = "\n".join(body_lines).strip()
                current["files"] = _aggregate_files(current_files)
                commits.append(current)
            current = {}
            current_files = []
            reading_body = False
            body_lines = []
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
                reading_body = True
                body_lines = [line[5:].strip()] if line[5:].strip() else []
            elif re.match(r"^(?:\d+|-)\s+(?:\d+|-)\s+\S", line):
                reading_body = False
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
            elif reading_body:
                body_lines.append(line)

    # Don't forget the last commit
    if current is not None:
        current["body"] = "\n".join(body_lines).strip()
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
