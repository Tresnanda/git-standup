"""GitHub API-backed commit retrieval for remote repositories."""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any

_GITHUB_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


class GitHubRateLimitError(RuntimeError):
    """Raised when GitHub reports primary or secondary API rate limiting."""


@dataclass
class GitHubApiRunCache:
    """Per-run GitHub API response cache and rate-limit skip accounting."""

    responses: dict[tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]], object] = field(
        default_factory=dict
    )
    hits: int = 0
    misses: int = 0
    rate_limited: bool = False
    commit_details_skipped: int = 0
    pull_request_enrichments_skipped: int = 0
    repositories: dict[str, dict[str, int | bool]] = field(default_factory=dict)
    default_since: str | None = None

    def key(
        self,
        endpoint: str,
        params: dict[str, str | int] | None,
        headers: list[str] | None,
    ) -> tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]]:
        return (
            endpoint,
            tuple(sorted((str(key), str(value)) for key, value in (params or {}).items())),
            tuple(headers or ()),
        )

    def repo_stats(self, repo_slug: str) -> dict[str, int | bool]:
        return self.repositories.setdefault(
            repo_slug,
            {
                "commit_details_skipped": 0,
                "pull_request_enrichments_skipped": 0,
                "rate_limited": False,
            },
        )

    def mark_rate_limited(self, repo_slug: str | None = None) -> None:
        self.rate_limited = True
        if repo_slug:
            self.repo_stats(repo_slug)["rate_limited"] = True

    def record_commit_detail_skip(self, repo_slug: str) -> None:
        self.commit_details_skipped += 1
        stats = self.repo_stats(repo_slug)
        stats["commit_details_skipped"] = int(stats["commit_details_skipped"]) + 1

    def record_pull_request_skip(self, repo_slug: str) -> None:
        self.pull_request_enrichments_skipped += 1
        stats = self.repo_stats(repo_slug)
        stats["pull_request_enrichments_skipped"] = (
            int(stats["pull_request_enrichments_skipped"]) + 1
        )

    def metadata(self) -> dict[str, Any] | None:
        """Return optional metadata for JSON/AI when caching or rate-limit skips matter."""
        if not (
            self.hits
            or self.rate_limited
            or self.commit_details_skipped
            or self.pull_request_enrichments_skipped
        ):
            return None

        metadata: dict[str, Any] = {
            "cache": {
                "hits": self.hits,
                "misses": self.misses,
            },
            "rate_limit": {
                "limited": self.rate_limited,
                "commit_detail_requests_skipped": self.commit_details_skipped,
                "pull_request_enrichments_skipped": self.pull_request_enrichments_skipped,
            },
        }
        if self.repositories:
            metadata["repositories"] = {
                repo: stats
                for repo, stats in self.repositories.items()
                if stats.get("rate_limited")
                or stats.get("commit_details_skipped")
                or stats.get("pull_request_enrichments_skipped")
            }
        return metadata


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
    cache: GitHubApiRunCache | None = None,
) -> list[dict[str, Any]]:
    """Fetch commits for a GitHub repository without cloning it locally.

    The implementation uses ``gh api`` so authentication and enterprise host
    config are delegated to the GitHub CLI. A per-run cache may be supplied by
    callers that fetch multiple repositories. Commits are collected from list
    pages first, then details/PRs are enriched progressively so optional detail
    calls can be skipped cleanly if GitHub starts rate-limiting the run.
    """
    run_cache = cache or GitHubApiRunCache()
    repo_slug = _normalize_repo_slug(repo)
    try:
        author_filter = _resolve_author_filter(author, cache=run_cache)
    except GitHubRateLimitError:
        run_cache.mark_rate_limited(repo_slug)
        return []
    page = 1
    per_page = 100
    if since:
        since_datetime = _to_github_datetime(since, end_of_day=False)
    else:
        if run_cache.default_since is None:
            run_cache.default_since = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat().replace("+00:00", "Z")
        since_datetime = run_cache.default_since
    params: dict[str, str | int] = {
        "per_page": per_page,
        "since": since_datetime,
    }
    if until:
        params["until"] = _to_github_datetime(until, end_of_day=True)

    matching_items: list[dict[str, Any]] = []
    while True:
        page_params = {**params, "page": page}
        page_endpoint = f"/repos/{repo_slug}/commits"
        page_cache_key = run_cache.key(page_endpoint, page_params, None)
        if run_cache.rate_limited and page_cache_key not in run_cache.responses:
            run_cache.mark_rate_limited(repo_slug)
            break
        page_items = _cached_gh_api_json(
            run_cache,
            page_endpoint,
            params=page_params,
            repo_slug=repo_slug,
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
            matching_items.append(item)
            if max_commits is not None and len(matching_items) >= max_commits:
                break

        if max_commits is not None and len(matching_items) >= max_commits:
            break
        if len(page_items) < per_page:
            break
        page += 1

    return [
        _commit_from_api_item(repo_slug, item, include_prs=include_prs, cache=run_cache)
        for item in matching_items
    ]


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


def _resolve_author_filter(author: str | None, *, cache: GitHubApiRunCache) -> str | None:
    if not author:
        return None
    if author != "me":
        return author
    user = _cached_gh_api_json(cache, "/user")
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
    cache: GitHubApiRunCache,
) -> dict[str, Any]:
    sha = str(item.get("sha") or "")
    api_metadata: dict[str, Any] = {}
    detail = item

    if sha:
        detail_endpoint = f"/repos/{repo_slug}/commits/{sha}"
        detail_cached = cache.key(detail_endpoint, None, None) in cache.responses
        if cache.rate_limited and not detail_cached:
            cache.record_commit_detail_skip(repo_slug)
            api_metadata["commit_detail"] = _skipped_metadata("rate_limit")
        else:
            try:
                fetched_detail = _cached_gh_api_json(
                    cache,
                    detail_endpoint,
                    repo_slug=repo_slug,
                )
            except GitHubRateLimitError:
                cache.record_commit_detail_skip(repo_slug)
                api_metadata["commit_detail"] = _skipped_metadata("rate_limit")
            else:
                if not isinstance(fetched_detail, dict):
                    raise RuntimeError(f"GitHub API returned unexpected commit detail for {sha}")
                detail = fetched_detail

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
    if include_prs and sha:
        pr_endpoint = f"/repos/{repo_slug}/commits/{sha}/pulls"
        pr_headers = ["Accept: application/vnd.github.groot-preview+json"]
        pr_cached = cache.key(pr_endpoint, None, pr_headers) in cache.responses
        if cache.rate_limited and not pr_cached:
            cache.record_pull_request_skip(repo_slug)
            api_metadata["pull_request_enrichment"] = _skipped_metadata("rate_limit")
        else:
            pr_info, skipped_reason = _pull_request_for_commit(repo_slug, sha, cache=cache)
            if skipped_reason:
                api_metadata["pull_request_enrichment"] = _skipped_metadata(skipped_reason)
            elif pr_info:
                parsed["pull_request"] = pr_info
    if api_metadata:
        parsed["github_api"] = api_metadata
    return parsed


def _skipped_metadata(reason: str) -> dict[str, Any]:
    return {"skipped": True, "reason": reason}


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


def _pull_request_for_commit(
    repo_slug: str,
    sha: str,
    *,
    cache: GitHubApiRunCache,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        prs = _cached_gh_api_json(
            cache,
            f"/repos/{repo_slug}/commits/{sha}/pulls",
            headers=["Accept: application/vnd.github.groot-preview+json"],
            repo_slug=repo_slug,
        )
    except GitHubRateLimitError:
        cache.record_pull_request_skip(repo_slug)
        return None, "rate_limit"
    except RuntimeError:
        return None, None
    if not isinstance(prs, list) or not prs:
        return None, None
    first = prs[0]
    if not isinstance(first, dict):
        return None, None
    try:
        number = int(first["number"])
    except (KeyError, TypeError, ValueError):
        return None, None
    info: dict[str, Any] = {"number": number, "source": "github-api"}
    if first.get("title"):
        info["title"] = str(first["title"])
    if first.get("html_url") or first.get("url"):
        info["url"] = str(first.get("html_url") or first.get("url"))
    return info, None


def _cached_gh_api_json(
    cache: GitHubApiRunCache,
    endpoint: str,
    *,
    params: dict[str, str | int] | None = None,
    headers: list[str] | None = None,
    repo_slug: str | None = None,
) -> object:
    key = cache.key(endpoint, params, headers)
    if key in cache.responses:
        cache.hits += 1
        return cache.responses[key]

    if cache.rate_limited:
        cache.mark_rate_limited(repo_slug)
        raise GitHubRateLimitError("GitHub API rate limit active; skipping uncached request")

    cache.misses += 1
    try:
        value = _gh_api_json(endpoint, params=params, headers=headers)
    except GitHubRateLimitError:
        cache.mark_rate_limited(repo_slug)
        raise
    except RuntimeError as exc:
        if _is_rate_limit_error(str(exc)):
            cache.mark_rate_limited(repo_slug)
            raise GitHubRateLimitError(str(exc)) from exc
        raise
    cache.responses[key] = value
    return value


def _is_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "rate limit" in lowered or "secondary rate" in lowered or "too many requests" in lowered


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
        message = f"GitHub API request failed for {endpoint}{detail}"
        if _is_rate_limit_error(message):
            raise GitHubRateLimitError(message)
        raise RuntimeError(message)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub API returned invalid JSON for {endpoint}") from exc
