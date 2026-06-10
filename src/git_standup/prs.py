"""Pull request metadata enrichment for standup commits."""

import json
import re
import shutil
import subprocess
from typing import Any

_GITHUB_REMOTE_RE = re.compile(
    r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
_MERGE_PR_RE = re.compile(r"^Merge pull request #(?P<number>\d+)\b", re.IGNORECASE)
_TRAILING_PR_RE = re.compile(r"\s*\(#(?P<number>\d+)\)\s*$")
_PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)"
)


def enrich_commits_with_prs(
    commits: list[dict[str, Any]],
    *,
    repo_path: str | None = None,
    repo_slug: str | None = None,
    query_github: bool = False,
    include_status: bool = False,
) -> list[dict[str, Any]]:
    """Return commits annotated with best-effort pull request metadata.

    Local commit subjects/bodies are inspected first. When ``query_github`` is true,
    GitHub CLI may be used to fill missing PR title/URL data or to find a PR by
    commit SHA. Callers should only pass ``query_github=True`` for an explicit
    user opt-in because it may perform network/API requests.

    When ``include_status`` is also true, ``gh pr view`` is asked for workflow
    status fields (draft state, checks, reviews, mergeability, labels, linked
    issues, and ownership). Failed or unavailable GitHub lookups are ignored so
    local summaries remain safe and deterministic.
    """
    repo_slug = repo_slug or _github_repo_slug(repo_path)
    gh = shutil.which("gh") if query_github else None
    annotated: list[dict[str, Any]] = []

    for commit in commits:
        item = dict(commit)
        pr_info = _local_pull_request_info(item, repo_slug)
        if gh and repo_slug:
            pr_info = _github_pull_request_info(
                gh,
                repo_slug,
                item,
                existing=pr_info,
                include_status=include_status,
            ) or pr_info
        if pr_info:
            item["pull_request"] = pr_info
        annotated.append(item)

    return annotated


def _github_repo_slug(repo_path: str | None) -> str | None:
    """Return owner/repo for GitHub origin remote, if available."""
    cmd = ["git"]
    if repo_path:
        cmd.extend(["-C", repo_path])
    cmd.extend(["remote", "get-url", "origin"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _slug_from_remote_url(result.stdout.strip())


def _slug_from_remote_url(remote_url: str) -> str | None:
    match = _GITHUB_REMOTE_RE.match(remote_url)
    if not match:
        return None
    owner = match.group("owner")
    repo = match.group("repo")
    return f"{owner}/{repo}"


def _local_pull_request_info(
    commit: dict[str, Any],
    repo_slug: str | None,
) -> dict[str, Any] | None:
    subject = str(commit.get("subject") or "").strip()
    body = str(commit.get("body") or "").strip()
    haystack = "\n".join(part for part in (subject, body) if part)
    number: int | None = None
    title = ""
    url = ""

    url_match = _PR_URL_RE.search(haystack)
    if url_match:
        number = int(url_match.group("number"))
        url = url_match.group(0)

    merge_match = _MERGE_PR_RE.match(subject)
    if merge_match:
        number = number or int(merge_match.group("number"))
        title = _first_body_line(body)

    trailing_match = _TRAILING_PR_RE.search(subject)
    if trailing_match:
        number = number or int(trailing_match.group("number"))
        title = _TRAILING_PR_RE.sub("", subject).strip()

    if number is None:
        return None
    if not title:
        title = _title_from_subject(subject)
    if not url and repo_slug:
        url = f"https://github.com/{repo_slug}/pull/{number}"

    return _pr_info(number=number, title=title, url=url, source="local")


def _github_pull_request_info(
    gh: str,
    repo_slug: str,
    commit: dict[str, Any],
    *,
    existing: dict[str, Any] | None,
    include_status: bool,
) -> dict[str, Any] | None:
    if existing and existing.get("number"):
        fetched = _gh_pr_view(
            gh,
            repo_slug,
            int(existing["number"]),
            include_status=include_status,
        )
    else:
        commit_hash = str(commit.get("hash") or "").strip()
        fetched = _gh_pr_for_commit(gh, repo_slug, commit_hash) if commit_hash else None
        if fetched and include_status:
            fetched = _gh_pr_view(
                gh,
                repo_slug,
                int(fetched["number"]),
                include_status=True,
            ) or fetched

    if not fetched:
        return existing
    if not existing:
        return {**fetched, "source": "github-cli"}

    merged = dict(existing)
    for key in (
        "title",
        "url",
        "state",
        "draft",
        "merge_state_status",
        "review_decision",
        "labels",
        "linked_issues",
        "checks",
        "updated_at",
        "author",
        "assignees",
        "review_requests",
    ):
        if fetched.get(key) is not None:
            merged[key] = fetched[key]
    merged["source"] = "github-cli"
    return merged


def _gh_pr_view(
    gh: str,
    repo_slug: str,
    number: int,
    *,
    include_status: bool = False,
) -> dict[str, Any] | None:
    fields = "number,title,url"
    if include_status:
        fields = ",".join(
            [
                "number",
                "title",
                "url",
                "state",
                "isDraft",
                "mergeStateStatus",
                "reviewDecision",
                "labels",
                "closingIssuesReferences",
                "statusCheckRollup",
                "updatedAt",
                "author",
                "assignees",
                "reviewRequests",
            ]
        )
    result = _run_gh(
        [
            gh,
            "pr",
            "view",
            str(number),
            "--repo",
            repo_slug,
            "--json",
            fields,
        ]
    )
    if not isinstance(result, dict):
        return None
    return _pr_info_from_gh(result)


def _gh_pr_for_commit(gh: str, repo_slug: str, commit_hash: str) -> dict[str, Any] | None:
    result = _run_gh(
        [
            gh,
            "pr",
            "list",
            "--repo",
            repo_slug,
            "--search",
            commit_hash,
            "--state",
            "all",
            "--limit",
            "1",
            "--json",
            "number,title,url",
        ]
    )
    if not isinstance(result, list) or not result:
        return None
    first = result[0]
    if not isinstance(first, dict):
        return None
    return _pr_info_from_gh(first)


def _run_gh(cmd: list[str]) -> object | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _pr_info_from_gh(data: dict[str, Any]) -> dict[str, Any] | None:
    try:
        number = int(data["number"])
    except (KeyError, TypeError, ValueError):
        return None
    info = _pr_info(
        number=number,
        title=str(data.get("title") or ""),
        url=str(data.get("url") or ""),
        source="github-cli",
    )
    _add_status_fields(info, data)
    return info


def _add_status_fields(info: dict[str, Any], data: dict[str, Any]) -> None:
    """Attach best-effort GitHub PR status fields from ``gh pr view`` JSON."""
    if data.get("state"):
        info["state"] = str(data["state"]).lower()
    if "isDraft" in data:
        info["draft"] = bool(data.get("isDraft"))
    if data.get("mergeStateStatus"):
        info["merge_state_status"] = str(data["mergeStateStatus"]).lower()
    if data.get("reviewDecision"):
        info["review_decision"] = str(data["reviewDecision"]).lower()
    labels = _names_from_items(data.get("labels"))
    if labels:
        info["labels"] = labels
    linked_issues = _issues_from_items(data.get("closingIssuesReferences"))
    if linked_issues:
        info["linked_issues"] = linked_issues
    checks = _checks_from_rollup(data.get("statusCheckRollup"))
    if checks:
        info["checks"] = checks
    if data.get("updatedAt"):
        info["updated_at"] = str(data["updatedAt"])
    author = data.get("author")
    if isinstance(author, dict) and author.get("login"):
        info["author"] = str(author["login"])
    assignees = _names_from_items(data.get("assignees"))
    if assignees:
        info["assignees"] = assignees
    review_requests = _names_from_items(data.get("reviewRequests"))
    if review_requests:
        info["review_requests"] = review_requests


def _names_from_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("login")
        if name:
            names.append(str(name))
    return names


def _issues_from_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    issues: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        issue: dict[str, Any] = {}
        if item.get("number") is not None:
            issue["number"] = item["number"]
        if item.get("title"):
            issue["title"] = str(item["title"])
        if item.get("url"):
            issue["url"] = str(item["url"])
        if issue:
            issues.append(issue)
    return issues


def _checks_from_rollup(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    total = len(value)
    if total == 0:
        return {"state": "none", "total": 0, "passed": 0, "failed": 0, "pending": 0}
    passed = 0
    failed = 0
    pending = 0
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("name"):
            names.append(str(item["name"]))
        conclusion = str(item.get("conclusion") or "").upper()
        status = str(item.get("status") or "").upper()
        if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            passed += 1
        elif conclusion in {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
            failed += 1
        elif status and status != "COMPLETED":
            pending += 1
    pending += max(total - passed - failed - pending, 0)
    if failed:
        state = "failed"
    elif pending:
        state = "pending"
    elif passed == total:
        state = "passed"
    else:
        state = "unknown"
    checks: dict[str, Any] = {
        "state": state,
        "total": total,
        "passed": passed,
        "failed": failed,
        "pending": pending,
    }
    if names:
        checks["names"] = names[:5]
    return checks


def _pr_info(*, number: int, title: str, url: str, source: str) -> dict[str, Any]:
    info: dict[str, Any] = {"number": number, "source": source}
    if title:
        info["title"] = title
    if url:
        info["url"] = url
    return info


def _first_body_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _title_from_subject(subject: str) -> str:
    if _MERGE_PR_RE.match(subject):
        return ""
    return _TRAILING_PR_RE.sub("", subject).strip()
