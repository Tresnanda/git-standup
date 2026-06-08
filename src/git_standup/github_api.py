"""GitHub API-backed commit retrieval for remote repositories."""

import json
import re
import shutil
import subprocess
from datetime import datetime, time, timedelta, timezone
from typing import Any

from git_standup.author_aliases import AuthorAliases

_GITHUB_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


def get_remote_commits(
    repo: str,
    *,
    days: int = 7,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_commits: int | None = None,
    exclude_merges: bool = False,
    include_prs: bool = False,
    author_aliases: AuthorAliases | None = None,
) -> list[dict[str, Any]]:
    """Fetch commits for a GitHub repository without cloning it locally.

    The implementation uses ``gh api`` so authentication, enterprise host config,
    and rate-limit handling are delegated to the GitHub CLI. Commit details are
    fetched for matching commits so output can include changed-file stats.
    """
    repo_slug = _normalize_repo_slug(repo)
    author_filter = _resolve_author_filter(author)
    if author_aliases is not None:
        author_filter = author_aliases.expand_filter(author_filter)
    commits: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    params: dict[str, str | int] = {
        "per_page": per_page,
        "since": _to_github_datetime(since, end_of_day=False)
        if since
        else (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    if until:
        params["until"] = _to_github_datetime(until, end_of_day=True)

    while True:
        page_items = _gh_api_json(
            f"/repos/{repo_slug}/commits",
            params={**params, "page": page},
        )
        if not isinstance(page_items, list):
            raise RuntimeError(f"GitHub API returned unexpected commit data for {repo_slug}")
        if not page_items:
            break

        for item in page_items:
            if not isinstance(item, dict):
                continue
            if exclude_merges and len(item.get("parents") or []) > 1:
                continue
            if author_filter and not _matches_author(item, author_filter):
                continue
            commits.append(_commit_from_api_item(repo_slug, item, include_prs=include_prs))
            if max_commits is not None and len(commits) >= max_commits:
                return commits

        if len(page_items) < per_page:
            break
        page += 1

    return commits


def validate_remote_api_options(*, base_branch: str | None, pathspecs: list[str] | None) -> None:
    """Raise a helpful error for git-native filters unsupported by API mode."""
    unsupported: list[str] = []
    if base_branch:
        unsupported.append("--base-branch")
    if pathspecs:
        unsupported.append("--path/--pathspec")
    if unsupported:
        joined = " and ".join(unsupported)
        raise RuntimeError(
            f"--remote-backend api does not support {joined}. "
            "Use --remote-backend clone for git-native filtering."
        )


def _normalize_repo_slug(repo: str) -> str:
    cleaned = repo.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix)
            break
    parts = cleaned.split("/")
    if len(parts) != 2 or not all(parts):
        raise RuntimeError(
            f"GitHub API backend requires a GitHub owner/repo remote, got {repo!r}"
        )
    return f"{parts[0]}/{parts[1]}"


def _to_github_datetime(value: str, *, end_of_day: bool) -> str:
    if not _GITHUB_DATETIME_RE.match(value):
        raise RuntimeError(f"Unsupported GitHub API date value: {value}")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
        parsed_time = time.max.replace(microsecond=0) if end_of_day else time.min
        return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = f"{normalized[:-5]}{normalized[-5:-2]}:{normalized[-2:]}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RuntimeError(f"Unsupported GitHub API date value: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_author_filter(author: str | None) -> str | None:
    if not author:
        return None
    if author != "me":
        return author
    user = _gh_api_json("/user")
    if not isinstance(user, dict) or not user.get("login"):
        raise RuntimeError("Could not resolve --author me from GitHub API")
    return str(user["login"])


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _matches_author(item: dict[str, Any], author: str) -> bool:
    patterns = [part.strip() for part in author.split("|") if part.strip()]
    if not patterns:
        return True
    commit = _as_dict(item.get("commit"))
    commit_author = _as_dict(commit.get("author"))
    github_author = _as_dict(item.get("author"))
    haystack = "\n".join(
        str(value)
        for value in (
            commit_author.get("name"),
            commit_author.get("email"),
            github_author.get("login"),
        )
        if value
    )
    return any(re.search(pattern, haystack, re.IGNORECASE) for pattern in patterns)


def _commit_from_api_item(
    repo_slug: str,
    item: dict[str, Any],
    *,
    include_prs: bool,
) -> dict[str, Any]:
    sha = str(item.get("sha") or "")
    detail = _gh_api_json(f"/repos/{repo_slug}/commits/{sha}") if sha else item
    if not isinstance(detail, dict):
        raise RuntimeError(f"GitHub API returned unexpected commit detail for {sha}")

    commit = _as_dict(detail.get("commit"))
    commit_author = _as_dict(commit.get("author"))
    github_author = _as_dict(detail.get("author"))
    subject, body = _split_commit_message(str(commit.get("message") or ""))
    parsed = {
        "hash": sha,
        "author_name": str(commit_author.get("name") or github_author.get("login") or "Unknown"),
        "author_email": str(commit_author.get("email") or ""),
        "date": str(commit_author.get("date") or ""),
        "subject": subject,
        "body": body,
        "files": _files_from_detail(detail),
    }
    if github_author.get("login"):
        parsed["author_login"] = str(github_author["login"])
    if include_prs and sha:
        pr_info = _pull_request_for_commit(repo_slug, sha)
        if pr_info:
            parsed["pull_request"] = pr_info
    return parsed


def _split_commit_message(message: str) -> tuple[str, str]:
    lines = message.splitlines()
    if not lines:
        return "", ""
    subject = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return subject, body


def _files_from_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    files = detail.get("files")
    if not isinstance(files, list):
        return []
    parsed: list[dict[str, Any]] = []
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        path = str(file_item.get("filename") or "unknown")
        parsed.append(
            {
                "path": path,
                "insertions": _int_or_zero(file_item.get("additions")),
                "deletions": _int_or_zero(file_item.get("deletions")),
            }
        )
    return parsed


def _int_or_zero(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _pull_request_for_commit(repo_slug: str, sha: str) -> dict[str, Any] | None:
    try:
        prs = _gh_api_json(
            f"/repos/{repo_slug}/commits/{sha}/pulls",
            headers=["Accept: application/vnd.github.groot-preview+json"],
        )
    except RuntimeError:
        return None
    if not isinstance(prs, list) or not prs:
        return None
    first = prs[0]
    if not isinstance(first, dict):
        return None
    try:
        number = int(first["number"])
    except (KeyError, TypeError, ValueError):
        return None
    info: dict[str, Any] = {"number": number, "source": "github-api"}
    if first.get("title"):
        info["title"] = str(first["title"])
    if first.get("html_url") or first.get("url"):
        info["url"] = str(first.get("html_url") or first.get("url"))
    return info


def _gh_api_json(
    endpoint: str,
    *,
    params: dict[str, str | int] | None = None,
    headers: list[str] | None = None,
) -> object:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("--remote-backend api requires the GitHub CLI (`gh`) to be installed")
    cmd = [
        gh,
        "api",
        "--method",
        "GET",
        endpoint,
        "-H",
        "Accept: application/vnd.github+json",
    ]
    for header in headers or []:
        cmd.extend(["-H", header])
    for key, value in (params or {}).items():
        cmd.extend(["-f", f"{key}={value}"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"GitHub API request failed for {endpoint}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"GitHub API request failed for {endpoint}{detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub API returned invalid JSON for {endpoint}") from exc
