"""Output formatting — pretty printing with Rich and JSON serialization."""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from rich.console import Console
from rich.markup import escape as escape_rich_markup
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

CHANGELOG_CATEGORIES = (
    "Features",
    "Fixes",
    "Docs",
    "Refactors",
    "Chores",
    "Other",
)

_CHANGELOG_TYPE_MAP = {
    "feat": "Features",
    "feature": "Features",
    "fix": "Fixes",
    "fixes": "Fixes",
    "bugfix": "Fixes",
    "docs": "Docs",
    "doc": "Docs",
    "refactor": "Refactors",
    "refactoring": "Refactors",
    "chore": "Chores",
    "ci": "Chores",
    "build": "Chores",
    "test": "Chores",
    "tests": "Chores",
    "style": "Chores",
    "lint": "Chores",
    "deps": "Chores",
    "dependency": "Chores",
    "dependencies": "Chores",
}

_CONVENTIONAL_SUBJECT_RE = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9-]*)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?:\s*(?P<description>.+)$"
)

_REPOSITORIES_KEY = "_repositories"
TEAM_DIGEST_TEMPLATES = ("slack", "github", "jira", "linear")
_MARKDOWN_TEXT_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]<>()|])")


def _escape_markdown_text(value: object) -> str:
    """Escape Markdown formatting delimiters in untrusted plain text."""
    return _MARKDOWN_TEXT_ESCAPE_RE.sub(r"\\\1", str(value))


def _markdown_code_span(value: object) -> str:
    """Wrap text in a Markdown code span, even when the text contains backticks."""
    text = str(value)
    if "`" not in text:
        return f"`{text}`"
    longest_run = max(len(match.group(0)) for match in re.finditer(r"`+", text))
    fence = "`" * (longest_run + 1)
    if text.startswith("`") or text.endswith("`"):
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def _markdown_link(label: object, url: object) -> str:
    """Build a Markdown link with an escaped label and a minimally safe target."""
    escaped_label = _escape_markdown_text(label)
    target = str(url).strip()
    if not target:
        return escaped_label
    safe_target = quote(target, safe=":/?#[]@!$&'*+,;=%-._~")
    return f"[{escaped_label}]({safe_target})"


def _repository_sections(
    commit_data: dict[str, Any],
) -> list[tuple[str | None, dict[str, Any]]]:
    repositories = commit_data.get(_REPOSITORIES_KEY)
    if isinstance(repositories, dict):
        return [(str(name), data) for name, data in repositories.items() if isinstance(data, dict)]
    return [(None, commit_data)]


def build_json_output(
    commit_data: dict[str, Any],
) -> str:
    """Serialize commit data to JSON."""
    return json.dumps(commit_data, indent=2, default=str)


def build_markdown_output(
    commit_data: dict[str, Any],
    budget_metadata: dict[str, Any] | None = None,
) -> str:
    """Build a paste-ready Markdown standup summary."""
    lines = ["# Standup Summary", ""]
    note = _format_budget_note(budget_metadata, markdown=True)
    if note:
        lines.extend([note, ""])

    for repo_name, repo_data in _repository_sections(commit_data):
        if repo_name is not None:
            lines.extend([f"## {_escape_markdown_text(repo_name)}", ""])
            author_heading = "###"
            date_heading = "####"
        else:
            author_heading = "##"
            date_heading = "###"

        for author, days in repo_data.items():
            lines.extend([f"{author_heading} {_escape_markdown_text(author)}", ""])
            for date_key, day_data in days.items():
                lines.extend([f"{date_heading} {_escape_markdown_text(date_key)}", ""])
                for commit in day_data.get("commits", []):
                    hash_short = commit.get("hash", "")[:8]
                    subject = _escape_markdown_text(commit.get("subject", ""))
                    lines.append(f"- {_markdown_code_span(hash_short)} {subject}")
                    pr_note = _format_pull_request_note(commit, markdown=True)
                    if pr_note:
                        lines.append(f"  - {pr_note}")
                    quality_note = _format_quality_note(commit, markdown=True)
                    if quality_note:
                        lines.append(f"  - {quality_note}")

                    files = commit.get("files", [])
                    if files:
                        lines.append("  - Files:")
                        for file_stat in files:
                            path = file_stat.get("path", "")
                            insertions = file_stat.get("insertions", 0)
                            deletions = file_stat.get("deletions", 0)
                            lines.append(
                                f"    - {_markdown_code_span(path)} (+{insertions}/-{deletions})"
                            )

                stats = day_data.get("stats", {})
                lines.extend(
                    [
                        "",
                        (
                            f"_Stats: {stats.get('total_commits', 0)} commit(s), "
                            f"{stats.get('total_files', 0)} file(s), "
                            f"+{stats.get('total_insertions', 0)}/"
                            f"-{stats.get('total_deletions', 0)} lines_"
                        ),
                        "",
                    ]
                )

    return "\n".join(lines).rstrip() + "\n"


def build_stats_output(
    commit_data: dict[str, Any],
    *,
    output_format: str = "text",
    budget_metadata: dict[str, Any] | None = None,
) -> str:
    """Build an aggregate-only standup stats report."""
    markdown = output_format == "markdown"
    lines = ["# Standup Stats", ""] if markdown else ["Standup Stats", ""]
    note = _format_budget_note(budget_metadata, markdown=markdown)
    if note:
        lines.extend([note, ""])
    grand = {"commits": 0, "files": set(), "insertions": 0, "deletions": 0}

    for repo_name, repo_data in _repository_sections(commit_data):
        if repo_name is not None:
            if markdown:
                lines.extend([f"## {_escape_markdown_text(repo_name)}", ""])
                author_heading = "###"
            else:
                lines.extend([f"Repository: {repo_name}", "=" * (12 + len(repo_name)), ""])
                author_heading = ""
        else:
            author_heading = "##" if markdown else ""

        for author, days in repo_data.items():
            if markdown:
                lines.extend([f"{author_heading} {_escape_markdown_text(author)}", ""])
            else:
                lines.extend([author, "-" * len(author)])

            author_total = {"commits": 0, "files": set(), "insertions": 0, "deletions": 0}
            for date_key, day_data in days.items():
                stats = day_data.get("stats", {})
                counts = _stats_counts(stats)
                _add_stats(author_total, counts)
                _add_stats(grand, _stats_counts(stats, namespace=repo_name))
                line = _format_stats_summary(date_key, counts)
                lines.append(f"- {line}" if markdown else f"  {line}")

            author_summary = _format_stats_summary("Total", author_total)
            lines.append(f"- **{author_summary}**" if markdown else f"  {author_summary}")
            lines.append("")

    grand_summary = _format_stats_summary("Total", grand)
    if markdown:
        lines.extend(["## Summary", "", f"- **{grand_summary}**"])
    else:
        lines.extend(["Summary", "-------", grand_summary])
    return "\n".join(lines).rstrip() + "\n"


def _stats_counts(stats: dict[str, Any], namespace: str | None = None) -> dict[str, Any]:
    files_changed = stats.get("files_changed", [])
    files = {
        f"{namespace}:{path}" if namespace else str(path)
        for path in files_changed
    }
    return {
        "commits": int(stats.get("total_commits", 0) or 0),
        "files": files,
        "insertions": int(stats.get("total_insertions", 0) or 0),
        "deletions": int(stats.get("total_deletions", 0) or 0),
    }


def _add_stats(target: dict[str, Any], counts: dict[str, Any]) -> None:
    target["commits"] += counts["commits"]
    target["files"].update(counts["files"])
    target["insertions"] += counts["insertions"]
    target["deletions"] += counts["deletions"]


def _format_stats_summary(label: str, counts: dict[str, Any]) -> str:
    return (
        f"{label}: {counts['commits']} commit(s), {len(counts['files'])} file(s), "
        f"+{counts['insertions']}/-{counts['deletions']} lines"
    )


def build_changelog_output(
    commits: list[dict[str, Any]],
    budget_metadata: dict[str, Any] | None = None,
) -> str:
    """Build release-note style Markdown grouped by conventional commit category."""
    categories: dict[str, list[dict[str, Any]]] = {
        category: [] for category in CHANGELOG_CATEGORIES
    }
    authors: set[str] = set()
    files_by_path: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"path": "", "insertions": 0, "deletions": 0, "commits": 0}
    )
    total_insertions = 0
    total_deletions = 0

    for commit in commits:
        author = commit.get("author_name")
        if author:
            authors.add(str(author))

        category, description, breaking = _changelog_commit_summary(commit)
        entry = {
            "commit": commit,
            "description": description,
            "breaking": breaking,
            "files": _sorted_commit_files(commit),
        }
        categories[category].append(entry)

        for file_stat in commit.get("files", []):
            path = str(file_stat.get("path") or "unknown")
            insertions = int(file_stat.get("insertions", 0) or 0)
            deletions = int(file_stat.get("deletions", 0) or 0)
            file_total = files_by_path[path]
            file_total["path"] = path
            file_total["insertions"] += insertions
            file_total["deletions"] += deletions
            file_total["commits"] += 1
            total_insertions += insertions
            total_deletions += deletions

    lines = ["# Changelog", ""]
    files_changed = len(files_by_path)
    author_count = len(authors)
    summary_parts = [
        f"{len(commits)} commit(s)",
        f"{files_changed} file(s) changed",
        f"+{total_insertions}/-{total_deletions} lines",
    ]
    if author_count:
        summary_parts.append(f"{author_count} author(s)")
    lines.extend([f"_{' · '.join(summary_parts)}_", ""])

    if budget_metadata and budget_metadata.get("truncated"):
        lines.extend([_format_changelog_budget_note(budget_metadata), ""])

    for category in CHANGELOG_CATEGORIES:
        entries = categories[category]
        if not entries:
            continue
        lines.extend([f"## {category}", ""])
        for entry in entries:
            commit = entry["commit"]
            hash_short = str(commit.get("hash", ""))[:8]
            stats_text = _format_commit_change_summary(entry["files"])
            prefix = "⚠️ " if entry["breaking"] else ""
            hash_text = f" ({_markdown_code_span(hash_short)})" if hash_short else ""
            lines.append(f"- {prefix}{entry['description']}{hash_text} — {stats_text}")

            file_highlights = _format_file_highlights(entry["files"])
            if file_highlights:
                lines.append(f"  - Files: {file_highlights}")
            truncated = commit.get("truncated", {})
            if truncated.get("files"):
                lines.append(
                    "  - Files omitted by `--max-files-per-commit`: "
                    f"{truncated.get('files_omitted', 0)}"
                )
            quality_note = _format_quality_note(commit, markdown=True)
            if quality_note:
                lines.append(f"  - {quality_note}")
            pr_note = _format_pull_request_note(commit, markdown=True)
            if pr_note:
                lines.append(f"  - {pr_note}")
        lines.append("")

    lines.extend(["## Change Stats", ""])
    lines.append(
        f"- Total: {len(commits)} commit(s), {files_changed} file(s), "
        f"+{total_insertions}/-{total_deletions} lines"
    )
    if authors:
        lines.append(
            f"- Authors: {', '.join(_escape_markdown_text(author) for author in sorted(authors))}"
        )
    top_files = _format_file_highlights(
        sorted(
            files_by_path.values(),
            key=lambda item: (
                int(item.get("insertions", 0) or 0) + int(item.get("deletions", 0) or 0),
                str(item.get("path", "")),
            ),
            reverse=True,
        ),
        limit=5,
    )
    if top_files:
        lines.append(f"- Top files: {top_files}")

    return "\n".join(lines).rstrip() + "\n"


def build_team_digest_output(
    commit_data: dict[str, Any],
    *,
    template: str = "slack",
    include_workflow_board: bool = False,
    stale_days: int = 7,
) -> str:
    """Build a non-AI team workflow digest grouped by owner/author."""
    if template not in TEAM_DIGEST_TEMPLATES:
        choices = ", ".join(TEAM_DIGEST_TEMPLATES)
        raise ValueError(f"unknown team digest template: {template!r} (choose {choices})")

    template_label = {
        "github": "GitHub",
        "jira": "Jira",
        "linear": "Linear",
        "slack": "Slack",
    }[template]
    owners = _team_digest_owners(commit_data)
    risks: list[dict[str, Any]] = []
    questions: list[str] = []
    lines = ["# Team Workflow Digest", "", f"_Template: {template_label}_", ""]

    for owner, owner_data in owners.items():
        commits = owner_data["commits"]
        files = owner_data["files"]
        insertions = owner_data["insertions"]
        deletions = owner_data["deletions"]
        summary = (
            f"- Commits: {len(commits)} · Files: {len(files)} · "
            f"Lines: +{insertions}/-{deletions}"
        )
        lines.extend(
            [
                f"## Owner: {_escape_markdown_text(owner)}",
                "",
                summary,
                "- Work evidence:",
            ]
        )
        for item in commits:
            commit = item["commit"]
            hash_short = _commit_hash_short(commit)
            subject = _escape_markdown_text(commit.get("subject") or "Untitled commit")
            repo_note = f" · {_escape_markdown_text(item['repo'])}" if item.get("repo") else ""
            date = _escape_markdown_text(item["date"])
            lines.append(f"  - {_markdown_code_span(hash_short)} {subject} ({date}{repo_note})")

            pr_note = _format_pull_request_note(commit, markdown=True)
            if pr_note:
                lines.append(f"    - {pr_note}")
                question = _team_digest_pr_question(owner, commit)
                if question:
                    questions.append(question)
            for issue_note in _format_issue_notes(commit):
                lines.append(f"    - {issue_note}")
                question = _team_digest_issue_question(owner, issue_note)
                if question:
                    questions.append(question)

            file_highlights = _format_file_highlights(_sorted_commit_files(commit), limit=2)
            if file_highlights:
                lines.append(f"    - Files: {file_highlights}")

            risk_reasons = _team_digest_risk_reasons(commit)
            if risk_reasons:
                risks.append({"owner": owner, "commit": commit, "reasons": risk_reasons})
                questions.append(_team_digest_risk_question(owner, commit, risk_reasons))
        lines.append("")

    lines.extend(["## Risk / Blocker Radar", ""])
    if risks:
        for risk in risks:
            commit = risk["commit"]
            lines.append(
                f"- {_escape_markdown_text(risk['owner'])}: "
                f"{_markdown_code_span(_commit_hash_short(commit))} "
                f"{_escape_markdown_text(commit.get('subject', 'Untitled commit'))} — "
                f"{_risk_reason_text(risk['reasons'])}"
            )
    else:
        lines.append("- No obvious WIP/revert/fix/low-signal risks found in the selected commits.")
    lines.append("")

    lines.extend(["## Follow-up Questions", ""])
    deduped_questions = _dedupe_preserving_order(questions)
    if deduped_questions:
        for question in deduped_questions[:8]:
            lines.append(f"- {question}")
    else:
        lines.append("- Any commits that need review, rollout notes, or owner confirmation?")

    if include_workflow_board:
        lines.extend(
            [
                "",
                *_workflow_board_lines(
                    commit_data,
                    stale_days=stale_days,
                    heading_level=2,
                ),
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def build_workflow_board_output(commit_data: dict[str, Any], *, stale_days: int = 7) -> str:
    """Build a standup-ready PR workflow board from enriched PR metadata."""
    lines = _workflow_board_lines(commit_data, stale_days=stale_days, heading_level=1)
    return "\n".join(lines) + "\n"


def _workflow_board_lines(
    commit_data: dict[str, Any],
    *,
    stale_days: int,
    heading_level: int,
) -> list[str]:
    heading = "#" * heading_level
    section_heading = "#" * (heading_level + 1)
    board = _workflow_board_items(commit_data, stale_days=stale_days)
    total_prs = sum(len(items) for items in board.values())
    lines = [
        f"{heading} Workflow Status Board",
        "",
        (
            "_PR handoff board using GitHub status metadata when available: draft state, "
            "checks, reviews, mergeability, labels, linked issues, and stale follow-ups._"
        ),
        "",
        f"- Pull requests: {total_prs}",
        f"- Stale threshold: {stale_days} day(s) since last PR update",
        "",
    ]
    sections = [
        ("needs_review", "Needs Review"),
        ("ready_to_merge", "Ready to Merge"),
        ("rollout", "Rollout"),
        ("owner_action", "Owner Action"),
    ]
    for key, title in sections:
        lines.extend([f"{section_heading} {title}", ""])
        items = board[key]
        if not items:
            lines.append("- None")
        else:
            for item in items:
                lines.extend(_format_workflow_board_item(item))
        lines.append("")
    return lines[:-1]


def _workflow_board_items(
    commit_data: dict[str, Any],
    *,
    stale_days: int,
) -> dict[str, list[dict[str, Any]]]:
    board: dict[str, list[dict[str, Any]]] = {
        "needs_review": [],
        "ready_to_merge": [],
        "rollout": [],
        "owner_action": [],
    }
    for item in _collect_pull_request_items(commit_data, stale_days=stale_days):
        board[_workflow_board_bucket(item)].append(item)
    return board


def _collect_pull_request_items(
    commit_data: dict[str, Any],
    *,
    stale_days: int,
) -> list[dict[str, Any]]:
    items_by_key: dict[str, dict[str, Any]] = {}
    for repo_name, repo_data in _repository_sections(commit_data):
        for owner, days in repo_data.items():
            for date_key, day_data in days.items():
                for commit in day_data.get("commits", []):
                    pull_request = commit.get("pull_request")
                    if not isinstance(pull_request, dict) or pull_request.get("number") is None:
                        continue
                    key = _workflow_pr_key(repo_name, pull_request)
                    item = items_by_key.setdefault(
                        key,
                        {
                            "repo": repo_name,
                            "owner": str(owner),
                            "pull_request": pull_request,
                            "commits": [],
                            "stale_days": stale_days,
                            "stale": _is_stale_pr(pull_request, stale_days=stale_days),
                        },
                    )
                    item["commits"].append({"date": str(date_key), "commit": commit})
                    if str(owner) not in str(item["owner"]).split(", "):
                        item["owner"] = f"{item['owner']}, {owner}"
    return list(items_by_key.values())


def _workflow_pr_key(repo_name: str | None, pull_request: dict[str, Any]) -> str:
    url = str(pull_request.get("url") or "").strip()
    if url:
        return url
    return f"{repo_name or ''}#{pull_request.get('number')}"


def _workflow_board_bucket(item: dict[str, Any]) -> str:
    pull_request = item["pull_request"]
    state = str(pull_request.get("state") or "").lower()
    draft = bool(pull_request.get("draft"))
    review = str(pull_request.get("review_decision") or "").lower()
    merge_state = str(pull_request.get("merge_state_status") or "").lower()
    checks = pull_request.get("checks") if isinstance(pull_request.get("checks"), dict) else {}
    check_state = str(checks.get("state") or "").lower()

    if state in {"merged"} or pull_request.get("merged_at"):
        return "rollout"
    if state == "closed":
        return "rollout"
    if (
        draft
        or review == "changes_requested"
        or check_state in {"failed", "pending"}
        or merge_state in {"dirty", "blocked", "behind", "has_hooks", "unknown"}
        or item.get("stale")
    ):
        return "owner_action"
    if review == "approved" and check_state in {"passed", "none", ""} and merge_state in {
        "",
        "clean",
        "unstable",
    }:
        return "ready_to_merge"
    return "needs_review"


def _format_workflow_board_item(item: dict[str, Any]) -> list[str]:
    pull_request = item["pull_request"]
    owner = _escape_markdown_text(item["owner"])
    repo_note = f" · {_escape_markdown_text(item['repo'])}" if item.get("repo") else ""
    title = str(pull_request.get("title") or "Untitled PR")
    number = pull_request.get("number")
    url = str(pull_request.get("url") or "").strip()
    label = f"#{number} {title}"
    linked = _markdown_link(label, url)
    status_bits = _workflow_status_bits(item)
    lines = [f"- {owner}: {linked}{repo_note} · {' · '.join(status_bits)}"]
    labels = pull_request.get("labels")
    if isinstance(labels, list) and labels:
        lines.append(
            f"  - Labels: {', '.join(_escape_markdown_text(label) for label in labels)}"
        )
    issues = pull_request.get("linked_issues")
    if isinstance(issues, list) and issues:
        lines.append(f"  - Linked issues: {_format_linked_issues(issues)}")
    commits = item.get("commits") if isinstance(item.get("commits"), list) else []
    if commits:
        first = commits[0]
        commit = first["commit"]
        lines.append(
            "  - Evidence: "
            f"{_markdown_code_span(_commit_hash_short(commit))} "
            f"{_escape_markdown_text(commit.get('subject', 'Untitled commit'))} "
            f"({_escape_markdown_text(first['date'])})"
        )
    action = _workflow_action_text(item)
    if action:
        lines.append(f"  - Follow-up: {action}")
    return lines


def _workflow_status_bits(item: dict[str, Any]) -> list[str]:
    pull_request = item["pull_request"]
    bits = [f"state: {str(pull_request.get('state') or 'unknown').lower()}"]
    bits.append("draft" if pull_request.get("draft") else "not draft")
    bits.append(f"checks: {_checks_summary(pull_request.get('checks'))}")
    bits.append(f"review: {str(pull_request.get('review_decision') or 'unknown').lower()}")
    bits.append(f"merge: {str(pull_request.get('merge_state_status') or 'unknown').lower()}")
    if pull_request.get("updated_at"):
        bits.append(f"updated: {str(pull_request['updated_at'])[:10]}")
    if item.get("stale"):
        bits.append(f"stale >{item['stale_days']}d")
    return bits


def _checks_summary(checks: object) -> str:
    if not isinstance(checks, dict):
        return "unknown"
    state = str(checks.get("state") or "unknown")
    total = checks.get("total")
    if total is None:
        return state
    return (
        f"{state} ({checks.get('passed', 0)} passed, {checks.get('failed', 0)} failed, "
        f"{checks.get('pending', 0)} pending)"
    )


def _format_linked_issues(issues: list[object]) -> str:
    parts: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        title = str(issue.get("title") or "").strip()
        url = str(issue.get("url") or "").strip()
        label = f"#{number}" if number is not None else title or url
        if title and number is not None:
            label = f"{label} {title}"
        parts.append(_markdown_link(label, url))
    return ", ".join(parts)


def _workflow_action_text(item: dict[str, Any]) -> str:
    pull_request = item["pull_request"]
    checks = pull_request.get("checks") if isinstance(pull_request.get("checks"), dict) else {}
    check_state = str(checks.get("state") or "").lower()
    review = str(pull_request.get("review_decision") or "").lower()
    merge_state = str(pull_request.get("merge_state_status") or "").lower()
    if pull_request.get("draft"):
        return "Owner to confirm whether the draft is ready for review."
    if review == "changes_requested":
        return "Owner to address requested review changes."
    if check_state == "failed":
        return "Owner to fix failing checks before handoff."
    if check_state == "pending":
        return "Owner to wait on or unblock pending checks."
    if merge_state in {"dirty", "blocked", "behind", "has_hooks", "unknown"}:
        return "Owner to resolve mergeability or branch protection blockers."
    if item.get("stale"):
        return "Stale follow-up: confirm current owner, review status, or rollout plan."
    if _workflow_board_bucket(item) == "ready_to_merge":
        return "Merge owner to merge and communicate rollout notes."
    if _workflow_board_bucket(item) == "rollout":
        return "Confirm deployment, release notes, and linked issue closure."
    return "Reviewer to review or assign next action."


def _is_stale_pr(pull_request: dict[str, Any], *, stale_days: int) -> bool:
    updated_at = str(pull_request.get("updated_at") or "").strip()
    if not updated_at:
        return False
    try:
        parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return age.days >= stale_days


def build_insights_output(commit_data: dict[str, Any]) -> str:
    """Build a concise, non-AI planning-insights report from commit data."""
    items = _insights_items(commit_data)
    authors = {item["author"] for item in items}
    repos = {item["repo"] for item in items if item.get("repo")}
    all_files: set[str] = set()
    total_insertions = 0
    total_deletions = 0
    themes: dict[str, dict[str, Any]] = {}
    areas: dict[str, dict[str, Any]] = {}
    risks: list[dict[str, Any]] = []
    follow_ups: list[str] = []

    for item in items:
        commit = item["commit"]
        files = _sorted_commit_files(commit)
        insertions = sum(int(file_stat.get("insertions", 0) or 0) for file_stat in files)
        deletions = sum(int(file_stat.get("deletions", 0) or 0) for file_stat in files)
        file_paths = {str(file_stat.get("path") or "unknown") for file_stat in files}
        all_files.update(file_paths)
        total_insertions += insertions
        total_deletions += deletions

        theme = _insights_theme(commit)
        theme_data = themes.setdefault(
            theme,
            {"commits": [], "files": set(), "insertions": 0, "deletions": 0, "repos": set()},
        )
        _insights_add_bucket_item(theme_data, item, file_paths, insertions, deletions)

        area = _insights_area(commit)
        area_data = areas.setdefault(
            area,
            {"commits": [], "files": set(), "insertions": 0, "deletions": 0, "repos": set()},
        )
        _insights_add_bucket_item(area_data, item, file_paths, insertions, deletions)

        risk_reasons = _insights_risk_reasons(commit, files, insertions + deletions)
        if risk_reasons:
            risks.append({"item": item, "reasons": risk_reasons})
            follow_ups.extend(_insights_risk_follow_ups(str(item["author"]), commit, risk_reasons))

        pr_follow_up = _insights_pr_follow_up(commit)
        if pr_follow_up:
            follow_ups.append(pr_follow_up)
        for issue_note in _format_issue_notes(commit):
            issue_follow_up = _team_digest_issue_question(str(item["author"]), issue_note)
            if issue_follow_up:
                follow_ups.append(issue_follow_up)

    lines = ["# Planning Insights", ""]
    repo_count = len(repos) if repos else 1
    lines.extend(
        [
            (
                f"_Scope: {len(items)} commit(s) · {len(authors)} author(s) · "
                f"{repo_count} repo(s) · {len(all_files)} file(s) · "
                f"+{total_insertions}/-{total_deletions} lines_"
            ),
            "",
        ]
    )

    lines.extend(["## Themes", ""])
    if themes:
        for label, data in _insights_sorted_buckets(themes):
            lines.append(_format_insights_bucket(label, data))
    else:
        lines.append("- No commit themes found in the selected range.")
    lines.append("")

    lines.extend(["## Likely Product Areas", ""])
    if areas:
        for label, data in _insights_sorted_buckets(areas):
            lines.append(_format_insights_bucket(label, data, include_files=True))
    else:
        lines.append("- No likely product areas found in the selected range.")
    lines.append("")

    lines.extend(["## Review / Rollout Risks", ""])
    if risks:
        for risk in risks[:8]:
            item = risk["item"]
            commit = item["commit"]
            repo_note = f" · {_escape_markdown_text(item['repo'])}" if item.get("repo") else ""
            lines.append(
                f"- {_escape_markdown_text(item['author'])}: "
                f"{_markdown_code_span(_commit_hash_short(commit))} "
                f"{_escape_markdown_text(commit.get('subject', 'Untitled commit'))}{repo_note} — "
                f"{_risk_reason_text(risk['reasons'])}"
            )
    else:
        lines.append("- No obvious WIP/revert/fix/low-signal or large-surface risks found.")
    lines.append("")

    lines.extend(["## Suggested Follow-ups", ""])
    deduped_follow_ups = _dedupe_preserving_order(follow_ups)
    if deduped_follow_ups:
        for question in deduped_follow_ups[:8]:
            lines.append(f"- {question}")
    else:
        lines.append(
            "- Confirm whether any theme needs review owners, rollout notes, "
            "or validation plans."
        )

    return "\n".join(lines).rstrip() + "\n"


def _insights_items(commit_data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for repo_name, repo_data in _repository_sections(commit_data):
        for author, days in repo_data.items():
            for date_key, day_data in days.items():
                for commit in day_data.get("commits", []):
                    items.append(
                        {
                            "repo": repo_name,
                            "author": str(author),
                            "date": str(date_key),
                            "commit": commit,
                        }
                    )
    return items


def _insights_add_bucket_item(
    bucket: dict[str, Any],
    item: dict[str, Any],
    file_paths: set[str],
    insertions: int,
    deletions: int,
) -> None:
    bucket["commits"].append(item)
    bucket["files"].update(file_paths)
    bucket["insertions"] += insertions
    bucket["deletions"] += deletions
    if item.get("repo"):
        bucket["repos"].add(item["repo"])


def _insights_sorted_buckets(
    buckets: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    return sorted(
        buckets.items(),
        key=lambda pair: (-len(pair[1]["commits"]), pair[0]),
    )


def _format_insights_bucket(
    label: str,
    data: dict[str, Any],
    *,
    include_files: bool = False,
) -> str:
    commits = data["commits"]
    summary = (
        f"- {label}: {len(commits)} commit(s), {len(data['files'])} file(s), "
        f"+{data['insertions']}/-{data['deletions']} lines"
    )
    repos = sorted(str(repo) for repo in data["repos"])
    if repos:
        summary += f" · repos: {', '.join(_escape_markdown_text(repo) for repo in repos[:3])}"
    if include_files and data["files"]:
        summary += f" · files: {_format_inline_paths(sorted(data['files'])[:3])}"
    evidence = _insights_evidence(commits)
    if evidence:
        summary += f" — {evidence}"
    return summary


def _insights_evidence(items: list[dict[str, Any]], limit: int = 2) -> str:
    evidence: list[str] = []
    for item in items[:limit]:
        commit = item["commit"]
        subject = _escape_markdown_text(commit.get("subject") or "Untitled commit")
        repo_note = (
            f" ({_escape_markdown_text(item['repo'])})" if item.get("repo") else ""
        )
        evidence.append(f"{_markdown_code_span(_commit_hash_short(commit))} {subject}{repo_note}")
    return "; ".join(evidence)


def _format_inline_paths(paths: list[str]) -> str:
    return ", ".join(_markdown_code_span(path) for path in paths)


def _insights_theme(commit: dict[str, Any]) -> str:
    subject = str(commit.get("subject") or "").strip()
    lower_text = f"{subject}\n{commit.get('body', '')}".lower()
    match = _CONVENTIONAL_SUBJECT_RE.match(subject)
    commit_type = match.group("type").lower() if match else subject.partition(":")[0].lower()
    if commit_type in {"feat", "feature"}:
        return "Feature work"
    if commit_type in {"fix", "bugfix", "hotfix"} or re.match(
        r"^(?:fix|fixes|fixed|hotfix)\b", lower_text
    ):
        return "Fixes and stabilization"
    if commit_type in {"docs", "doc"}:
        return "Documentation"
    if commit_type in {"test", "tests", "qa"}:
        return "Tests and quality"
    if commit_type == "refactor":
        return "Refactors"
    if commit_type in {"chore", "ci", "build", "deps", "dependency", "dependencies"}:
        return "Maintenance"
    if re.search(r"\b(?:wip|work in progress|blocked?|blocker)\b", lower_text):
        return "WIP and handoff"
    if re.search(r"\b(?:revert|reverted|reverting|rollback)\b", lower_text):
        return "Rollbacks and reversions"
    return "Other planning signal"


def _insights_area(commit: dict[str, Any]) -> str:
    paths = "\n".join(str(file_stat.get("path") or "") for file_stat in commit.get("files", []))
    text = f"{commit.get('subject', '')}\n{commit.get('body', '')}\n{paths}".lower()
    area_patterns = (
        ("Auth/Security", r"\b(?:auth|login|oauth|passkey|password|permission|security|token)\b"),
        ("Docs/Enablement", r"\b(?:docs?|readme|guide|onboarding|runbook)\b"),
        ("Frontend/UI", r"\b(?:frontend|web|ui|component|page|react|vue|tsx|css|dashboard)\b"),
        ("API/Backend", r"\b(?:api|backend|server|controller|route|endpoint|service)\b"),
        ("Data/Storage", r"\b(?:db|database|migration|sql|model|schema|storage)\b"),
        ("Infrastructure/CI", r"\b(?:infra|deploy|docker|k8s|ci|workflow|terraform)\b"),
        ("Tests/Quality", r"\b(?:test|tests|spec|pytest|qa|flaky)\b"),
        ("Build/Dependencies", r"\b(?:package|requirements|pyproject|build|deps?|dependency)\b"),
    )
    for label, pattern in area_patterns:
        if re.search(pattern, text):
            return label
    return "General product surface"


def _insights_risk_reasons(
    commit: dict[str, Any],
    files: list[dict[str, Any]],
    line_delta: int,
) -> list[str]:
    reasons = _team_digest_risk_reasons(commit)
    if len(files) >= 5:
        reasons.append(f"large file surface: {len(files)} files")
    if line_delta >= 250:
        reasons.append(f"large line delta: {line_delta} lines")
    return reasons


def _insights_pr_follow_up(commit: dict[str, Any]) -> str:
    pull_request = commit.get("pull_request")
    if not isinstance(pull_request, dict) or pull_request.get("number") is None:
        return ""
    title = str(pull_request.get("title") or "").strip()
    title_text = f" ({_escape_markdown_text(title)})" if title else ""
    return f"Confirm reviewer/merge plan for PR #{pull_request['number']}{title_text}."


def _insights_risk_follow_ups(
    owner: str,
    commit: dict[str, Any],
    reasons: list[str],
) -> list[str]:
    questions = [_team_digest_risk_question(owner, commit, reasons)]
    hash_short = _commit_hash_short(commit)
    if any(reason == "keyword: fix" for reason in reasons):
        questions.append(
            f"{_escape_markdown_text(owner)}: What validation confirms "
            f"{_markdown_code_span(hash_short)} fixed the issue?"
        )
    if any(reason.startswith("large ") for reason in reasons):
        questions.append(
            f"{_escape_markdown_text(owner)}: Does {_markdown_code_span(hash_short)} "
            "need staged rollout or extra review coverage?"
        )
    return questions


def _team_digest_owners(commit_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    owners: dict[str, dict[str, Any]] = {}
    for repo_name, repo_data in _repository_sections(commit_data):
        for author, days in repo_data.items():
            owner_data = owners.setdefault(
                str(author),
                {"commits": [], "files": set(), "insertions": 0, "deletions": 0},
            )
            for date_key, day_data in days.items():
                for commit in day_data.get("commits", []):
                    owner_data["commits"].append(
                        {"repo": repo_name, "date": str(date_key), "commit": commit}
                    )
                    for file_stat in commit.get("files", []):
                        path = str(file_stat.get("path") or "unknown")
                        owner_data["files"].add(
                            f"{repo_name}:{path}" if repo_name else path
                        )
                        owner_data["insertions"] += int(file_stat.get("insertions", 0) or 0)
                        owner_data["deletions"] += int(file_stat.get("deletions", 0) or 0)
    return owners


def _commit_hash_short(commit: dict[str, Any]) -> str:
    return str(commit.get("hash", ""))[:8]


def _format_issue_notes(commit: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    issues = commit.get("issues")
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue_id = str(issue.get("id") or issue.get("number") or "").strip()
            url = str(issue.get("url") or "").strip()
            title = str(issue.get("title") or "").strip()
            if not issue_id and not url:
                continue
            label = issue_id or url
            if title:
                label = f"{label} {title}"
            escaped_label = _escape_markdown_text(label)
            notes.append(
                f"Issue: {_markdown_link(label, url)}" if url else f"Issue: {escaped_label}"
            )

    text = f"{commit.get('subject', '')}\n{commit.get('body', '')}"
    existing_urls = {note.partition("(")[2].rstrip(")") for note in notes}
    for url in re.findall(r"https?://\S+", text):
        clean_url = url.rstrip(".)],")
        if clean_url in existing_urls:
            continue
        if any(marker in clean_url.lower() for marker in ("/issues/", "/browse/", "linear.app")):
            notes.append(f"Issue: {_markdown_link(clean_url, clean_url)}")
    return notes


def _team_digest_risk_reasons(commit: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    quality = commit.get("quality")
    if isinstance(quality, dict) and quality.get("signal") == "low":
        reasons.append("low-signal")

    text = f"{commit.get('subject', '')}\n{commit.get('body', '')}".lower()
    keyword_patterns = (
        ("wip", r"\b(?:wip|work in progress|blocked?|blocker)\b"),
        ("revert", r"\b(?:revert|reverted|reverting|rollback)\b"),
        ("fix", r"\b(?:fix|fixes|fixed|hotfix|flaky)\b"),
    )
    for keyword, pattern in keyword_patterns:
        if re.search(pattern, text):
            reasons.append(f"keyword: {keyword}")
    return reasons


def _risk_reason_text(reasons: list[str]) -> str:
    return ", ".join(reasons)


def _team_digest_pr_question(owner: str, commit: dict[str, Any]) -> str:
    pull_request = commit.get("pull_request")
    if not isinstance(pull_request, dict) or pull_request.get("number") is None:
        return ""
    title = str(pull_request.get("title") or "").strip()
    title_text = f" ({_escape_markdown_text(title)})" if title else ""
    return (
        f"{_escape_markdown_text(owner)}: Is PR #{pull_request['number']}{title_text} "
        "ready for review, merge, or follow-up?"
    )


def _team_digest_issue_question(owner: str, issue_note: str) -> str:
    match = re.search(r"\[([^\]]+)\]", issue_note)
    issue_label = match.group(1).split()[0] if match else _escape_markdown_text(
        issue_note.replace("Issue: ", "")
    )
    return (
        f"{_escape_markdown_text(owner)}: Does {issue_label} "
        "need status, owner, or acceptance follow-up?"
    )


def _team_digest_risk_question(
    owner: str,
    commit: dict[str, Any],
    reasons: list[str],
) -> str:
    hash_short = _commit_hash_short(commit)
    if any(reason == "keyword: wip" for reason in reasons):
        return (
            f"{_escape_markdown_text(owner)}: Is {_markdown_code_span(hash_short)} "
            "still in progress or blocking handoff?"
        )
    if any(reason == "keyword: revert" for reason in reasons):
        return (
            f"{_escape_markdown_text(owner)}: Does {_markdown_code_span(hash_short)} "
            "need rollback context or follow-up remediation?"
        )
    if any(reason == "keyword: fix" for reason in reasons):
        return (
            f"{_escape_markdown_text(owner)}: What validation confirms "
            f"{_markdown_code_span(hash_short)} fixed the issue?"
        )
    return (
        f"{_escape_markdown_text(owner)}: Can {_markdown_code_span(hash_short)} "
        "be clarified for reviewer or handoff context?"
    )


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _changelog_commit_summary(commit: dict[str, Any]) -> tuple[str, str, bool]:
    """Return (category, release-note description, breaking) for one commit."""
    subject = str(commit.get("subject", "")).strip() or "Untitled commit"
    body = str(commit.get("body", ""))
    match = _CONVENTIONAL_SUBJECT_RE.match(subject)
    body_breaking = "BREAKING CHANGE:" in body or "BREAKING-CHANGE:" in body
    if not match:
        return "Other", _escape_markdown_text(subject), body_breaking

    commit_type = match.group("type").lower()
    category = _CHANGELOG_TYPE_MAP.get(commit_type, "Other")
    scope = match.group("scope")
    description = _escape_markdown_text(match.group("description").strip())
    if scope:
        description = f"**{_escape_markdown_text(scope)}:** {description}"
    breaking = bool(match.group("breaking")) or body_breaking
    return category, description, breaking


def _sorted_commit_files(commit: dict[str, Any]) -> list[dict[str, Any]]:
    """Return changed files ordered by largest line delta, then path."""
    return sorted(
        commit.get("files", []),
        key=lambda item: (
            int(item.get("insertions", 0) or 0) + int(item.get("deletions", 0) or 0),
            str(item.get("path", "")),
        ),
        reverse=True,
    )


def _format_quality_note(commit: dict[str, Any], *, markdown: bool = False) -> str:
    quality = commit.get("quality")
    if not isinstance(quality, dict) or quality.get("signal") != "low":
        return ""
    reasons = quality.get("reasons", [])
    if isinstance(reasons, list) and reasons:
        if markdown:
            reason_text = "; ".join(_escape_markdown_text(reason) for reason in reasons)
        else:
            reason_text = "; ".join(str(reason) for reason in reasons)
        return f"⚠️ Low-signal commit message: {reason_text}."
    return "⚠️ Low-signal commit message."


def _format_pull_request_note(commit: dict[str, Any], *, markdown: bool) -> str:
    pull_request = commit.get("pull_request")
    if not isinstance(pull_request, dict):
        return ""
    number = pull_request.get("number")
    if number is None:
        return ""
    title = str(pull_request.get("title") or "").strip()
    url = str(pull_request.get("url") or "").strip()
    label = f"#{number}"
    if title:
        label = f"{label} {title if markdown else title}"
    if url:
        if markdown:
            return f"PR: {_markdown_link(label, url)}"
        return f"PR: {label} ({url})"
    if markdown:
        return f"PR: {_escape_markdown_text(label)}"
    return f"PR: {label}"


def _format_commit_change_summary(files: list[dict[str, Any]]) -> str:
    """Format per-commit changed-file/line stats for changelog bullets."""
    insertions = sum(int(file_stat.get("insertions", 0) or 0) for file_stat in files)
    deletions = sum(int(file_stat.get("deletions", 0) or 0) for file_stat in files)
    unique_files = {str(file_stat.get("path") or "unknown") for file_stat in files}
    if not unique_files:
        return "no file stats"
    return f"{len(unique_files)} file(s), +{insertions}/-{deletions} lines"


def _format_file_highlights(files: list[dict[str, Any]], limit: int = 3) -> str:
    """Format a compact list of changed files for Markdown output."""
    highlights: list[str] = []
    for file_stat in files[:limit]:
        path = str(file_stat.get("path") or "unknown")
        insertions = int(file_stat.get("insertions", 0) or 0)
        deletions = int(file_stat.get("deletions", 0) or 0)
        highlights.append(f"{_markdown_code_span(path)} (+{insertions}/-{deletions})")
    remaining = len(files) - limit
    if remaining > 0:
        highlights.append(f"+{remaining} more")
    return ", ".join(highlights)


def _budget_note_parts(budget_metadata: dict[str, Any]) -> list[str]:
    limits = budget_metadata.get("limits", {})
    notes: list[str] = []
    if budget_metadata.get("commits_truncated"):
        notes.append(f"commit list limited to {limits.get('max_commits')} commit(s)")
    if budget_metadata.get("files_truncated"):
        notes.append(
            "file lists limited to "
            f"{limits.get('max_files_per_commit')} file(s) per commit "
            f"({budget_metadata.get('files_omitted', 0)} omitted)"
        )
    return notes


def _format_budget_note(budget_metadata: dict[str, Any] | None, *, markdown: bool) -> str:
    """Format output-budget metadata for user-facing raw report notes."""
    if not budget_metadata:
        return ""

    notes = _budget_note_parts(budget_metadata)
    repo_notes = budget_metadata.get("repositories")
    if isinstance(repo_notes, dict):
        for repo_name, repo_metadata in repo_notes.items():
            if not isinstance(repo_metadata, dict) or not repo_metadata.get("truncated"):
                continue
            parts = _budget_note_parts(repo_metadata)
            if parts:
                repo_label = (
                    _escape_markdown_text(repo_name) if markdown else str(repo_name)
                )
                notes.append(f"{repo_label}: " + "; ".join(parts))

    if not notes:
        return ""
    text = "Note: output was truncated - " + "; ".join(notes) + "."
    return f"_{text}_" if markdown else text


def _format_changelog_budget_note(budget_metadata: dict[str, Any]) -> str:
    """Format output-budget metadata as a human-readable Markdown note."""
    note = _format_budget_note(budget_metadata, markdown=True)
    return note.replace(" - ", " — ", 1)


def build_text_output(
    commit_data: dict[str, Any],
    budget_metadata: dict[str, Any] | None = None,
) -> str:
    """Build a plain text standup summary suitable for files and terminals."""
    lines = ["Weekly Standup Summary", ""]
    note = _format_budget_note(budget_metadata, markdown=False)
    if note:
        lines.extend([note, ""])
    total_commits = 0
    total_insertions = 0
    total_deletions = 0
    all_files: set[str] = set()

    for repo_name, repo_data in _repository_sections(commit_data):
        if repo_name is not None:
            lines.extend([f"Repository: {repo_name}", "=" * (12 + len(repo_name)), ""])

        for author, days in repo_data.items():
            lines.extend([author, "-" * len(author)])
            author_insertions = 0
            author_deletions = 0
            author_commits = 0

            for date_key, day_data in days.items():
                lines.append(f"  {date_key}")
                for commit in day_data.get("commits", []):
                    author_commits += 1
                    hash_short = commit.get("hash", "")[:8]
                    subject = commit.get("subject", "")
                    lines.append(f"    [{hash_short}] {subject}")
                    pr_note = _format_pull_request_note(commit, markdown=False)
                    if pr_note:
                        lines.append(f"      {pr_note}")
                    quality_note = _format_quality_note(commit)
                    if quality_note:
                        lines.append(f"      {quality_note}")

                    for file_stat in commit.get("files", []):
                        path = file_stat.get("path", "")
                        insertions = file_stat.get("insertions", 0)
                        deletions = file_stat.get("deletions", 0)
                        all_files.add(path)
                        author_insertions += insertions
                        author_deletions += deletions
                        lines.append(f"      - {path} (+{insertions}/-{deletions})")

                    body = commit.get("body", "")
                    if body and body.strip():
                        lines.append(f"      {body.strip().splitlines()[0][:120]}")
                lines.append("")

            total_commits += author_commits
            total_insertions += author_insertions
            total_deletions += author_deletions
            lines.append(
                f"  Stats: {author_commits} commit(s), "
                f"+{author_insertions}/-{author_deletions} lines"
            )
            lines.append("")

    lines.extend(
        [
            "Summary",
            "-------",
            f"Total Commits: {total_commits}",
            f"Total Files Changed: {len(all_files)}",
            f"Total Lines Added: +{total_insertions}",
            f"Total Lines Removed: -{total_deletions}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def print_text_standup(
    commit_data: dict[str, Any],
) -> None:
    """Print a human-readable text standup (without AI), grouped by author and date."""
    console = Console()

    total_commits = 0
    total_insertions = 0
    total_deletions = 0
    all_files: set[str] = set()

    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]📋 Weekly Standup Summary[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print()

    for author, days in commit_data.items():
        console.print(f"[bold yellow]👤 {escape_rich_markup(str(author))}[/bold yellow]")
        console.print("[dim]─[/dim]" * 50)

        author_insertions = 0
        author_deletions = 0
        author_commits = 0

        for date_key, day_data in days.items():
            # Parse and format the date nicely
            try:
                dt = datetime.fromisoformat(date_key)
                date_display = dt.strftime("%a %b %d, %Y")
            except (ValueError, TypeError):
                date_display = date_key

            console.print(f"  [bold green]📅 {escape_rich_markup(str(date_display))}[/bold green]")

            for c in day_data.get("commits", []):
                author_commits += 1
                hash_short = escape_rich_markup(str(c.get("hash", ""))[:8])
                subject = escape_rich_markup(str(c.get("subject", "")))

                ins = sum(f.get("insertions", 0) for f in c.get("files", []))
                dels = sum(f.get("deletions", 0) for f in c.get("files", []))

                author_insertions += ins
                author_deletions += dels

                console.print(
                    f"    [{hash_short}] [bold]{subject}[/bold]"
                )
                pr_note = _format_pull_request_note(c, markdown=False)
                if pr_note:
                    console.print(f"      [cyan]{escape_rich_markup(pr_note)}[/cyan]")
                quality_note = _format_quality_note(c)
                if quality_note:
                    console.print(f"      [yellow]{escape_rich_markup(quality_note)}[/yellow]")

                # Show file changes
                for f in c.get("files", []):
                    path = f.get("path", "")
                    path_text = escape_rich_markup(str(path))
                    fi = f.get("insertions", 0)
                    fd = f.get("deletions", 0)
                    all_files.add(path)
                    stats_parts = []
                    if fi > 0:
                        stats_parts.append(f"[green]+{fi}[/green]")
                    if fd > 0:
                        stats_parts.append(f"[red]-{fd}[/red]")
                    stats_str = " ".join(stats_parts)
                    if stats_str:
                        console.print(f"      [dim]├[/dim] {path_text} ({stats_str})")
                    else:
                        console.print(f"      [dim]├[/dim] {path_text}")

                # Show commit body if present (short)
                body = c.get("body", "")
                if body and body.strip():
                    # Show first line of body
                    first_line = body.strip().split("\n")[0][:120]
                    if first_line:
                        console.print(
                            f"      [dim]└[/dim] [italic]{escape_rich_markup(first_line)}[/italic]"
                        )

            console.print()

        total_commits += author_commits
        total_insertions += author_insertions
        total_deletions += author_deletions

        console.print(
            f"  [dim]Stats: {author_commits} commit(s), "
            f"+{author_insertions}/-{author_deletions} lines[/dim]"
        )
        console.print()

    # Summary footer
    console.print(Rule(style="cyan"))
    summary = Table.grid(padding=(0, 2))
    summary.add_column()
    summary.add_column()

    summary.add_row(
        "[bold]Total Commits[/bold]",
        str(total_commits),
    )
    summary.add_row(
        "[bold]Total Files Changed[/bold]",
        str(len(all_files)),
    )
    summary.add_row(
        "[bold]Total Lines Added[/bold]",
        f"[green]+{total_insertions}[/green]",
    )
    summary.add_row(
        "[bold]Total Lines Removed[/bold]",
        f"[red]-{total_deletions}[/red]",
    )

    console.print(Panel(summary, title="[bold]📊 Summary[/bold]", border_style="cyan"))
    console.print()


def print_ai_standup(text: str) -> None:
    """Print the AI-generated standup text."""
    console = Console()
    console.print()
    console.print(
        Panel.fit(
            "[bold magenta]🤖 AI-Generated Standup[/bold magenta]",
            border_style="magenta",
        )
    )
    console.print()
    console.print(escape_rich_markup(text))
    console.print()
