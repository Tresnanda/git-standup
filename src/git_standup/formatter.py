"""Output formatting — pretty printing with Rich and JSON serialization."""

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from rich.console import Console
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
) -> str:
    """Build a paste-ready Markdown standup summary."""
    lines = ["# Standup Summary", ""]

    for repo_name, repo_data in _repository_sections(commit_data):
        if repo_name is not None:
            lines.extend([f"## {repo_name}", ""])
            author_heading = "###"
            date_heading = "####"
        else:
            author_heading = "##"
            date_heading = "###"

        for author, days in repo_data.items():
            lines.extend([f"{author_heading} {author}", ""])
            for date_key, day_data in days.items():
                lines.extend([f"{date_heading} {date_key}", ""])
                for commit in day_data.get("commits", []):
                    hash_short = commit.get("hash", "")[:8]
                    subject = commit.get("subject", "")
                    lines.append(f"- `{hash_short}` {subject}")
                    pr_note = _format_pull_request_note(commit, markdown=True)
                    if pr_note:
                        lines.append(f"  - {pr_note}")
                    quality_note = _format_quality_note(commit)
                    if quality_note:
                        lines.append(f"  - {quality_note}")

                    files = commit.get("files", [])
                    if files:
                        lines.append("  - Files:")
                        for file_stat in files:
                            path = file_stat.get("path", "")
                            insertions = file_stat.get("insertions", 0)
                            deletions = file_stat.get("deletions", 0)
                            lines.append(f"    - `{path}` (+{insertions}/-{deletions})")

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
) -> str:
    """Build an aggregate-only standup stats report."""
    markdown = output_format == "markdown"
    lines = ["# Standup Stats", ""] if markdown else ["Standup Stats", ""]
    grand = {"commits": 0, "files": set(), "insertions": 0, "deletions": 0}

    for repo_name, repo_data in _repository_sections(commit_data):
        if repo_name is not None:
            if markdown:
                lines.extend([f"## {repo_name}", ""])
                author_heading = "###"
            else:
                lines.extend([f"Repository: {repo_name}", "=" * (12 + len(repo_name)), ""])
                author_heading = ""
        else:
            author_heading = "##" if markdown else ""

        for author, days in repo_data.items():
            if markdown:
                lines.extend([f"{author_heading} {author}", ""])
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
            hash_text = f" (`{hash_short}`)" if hash_short else ""
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
            quality_note = _format_quality_note(commit)
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
        lines.append(f"- Authors: {', '.join(sorted(authors))}")
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
                f"## Owner: {owner}",
                "",
                summary,
                "- Work evidence:",
            ]
        )
        for item in commits:
            commit = item["commit"]
            hash_short = _commit_hash_short(commit)
            subject = str(commit.get("subject") or "Untitled commit")
            repo_note = f" · {item['repo']}" if item.get("repo") else ""
            lines.append(f"  - `{hash_short}` {subject} ({item['date']}{repo_note})")

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
                f"- {risk['owner']}: `{_commit_hash_short(commit)}` "
                f"{commit.get('subject', 'Untitled commit')} — "
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

    return "\n".join(lines).rstrip() + "\n"


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
            notes.append(f"Issue: [{label}]({url})" if url else f"Issue: {label}")

    text = f"{commit.get('subject', '')}\n{commit.get('body', '')}"
    existing_urls = {note.partition("(")[2].rstrip(")") for note in notes}
    for url in re.findall(r"https?://\S+", text):
        clean_url = url.rstrip(".)],")
        if clean_url in existing_urls:
            continue
        if any(marker in clean_url.lower() for marker in ("/issues/", "/browse/", "linear.app")):
            notes.append(f"Issue: [{clean_url}]({clean_url})")
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
    title_text = f" ({title})" if title else ""
    return (
        f"{owner}: Is PR #{pull_request['number']}{title_text} "
        "ready for review, merge, or follow-up?"
    )


def _team_digest_issue_question(owner: str, issue_note: str) -> str:
    match = re.search(r"\[([^\]]+)\]", issue_note)
    issue_label = match.group(1).split()[0] if match else issue_note.replace("Issue: ", "")
    return f"{owner}: Does {issue_label} need status, owner, or acceptance follow-up?"


def _team_digest_risk_question(
    owner: str,
    commit: dict[str, Any],
    reasons: list[str],
) -> str:
    hash_short = _commit_hash_short(commit)
    if any(reason == "keyword: wip" for reason in reasons):
        return f"{owner}: Is `{hash_short}` still in progress or blocking handoff?"
    if any(reason == "keyword: revert" for reason in reasons):
        return f"{owner}: Does `{hash_short}` need rollback context or follow-up remediation?"
    if any(reason == "keyword: fix" for reason in reasons):
        return f"{owner}: What validation confirms `{hash_short}` fixed the issue?"
    return f"{owner}: Can `{hash_short}` be clarified for reviewer or handoff context?"


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
        return "Other", subject, body_breaking

    commit_type = match.group("type").lower()
    category = _CHANGELOG_TYPE_MAP.get(commit_type, "Other")
    scope = match.group("scope")
    description = match.group("description").strip()
    if scope:
        description = f"**{scope}:** {description}"
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


def _format_quality_note(commit: dict[str, Any]) -> str:
    quality = commit.get("quality")
    if not isinstance(quality, dict) or quality.get("signal") != "low":
        return ""
    reasons = quality.get("reasons", [])
    if isinstance(reasons, list) and reasons:
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
        label = f"{label} {title}"
    if url:
        if markdown:
            return f"PR: [{label}]({url})"
        return f"PR: {label} ({url})"
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
        highlights.append(f"`{path}` (+{insertions}/-{deletions})")
    remaining = len(files) - limit
    if remaining > 0:
        highlights.append(f"+{remaining} more")
    return ", ".join(highlights)


def _format_changelog_budget_note(budget_metadata: dict[str, Any]) -> str:
    """Format output-budget metadata as a human-readable Markdown note."""
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
    return "_Note: output was truncated — " + "; ".join(notes) + "._"


def build_text_output(
    commit_data: dict[str, Any],
) -> str:
    """Build a plain text standup summary suitable for files and terminals."""
    lines = ["Weekly Standup Summary", ""]
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
        console.print(f"[bold yellow]👤 {author}[/bold yellow]")
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

            console.print(f"  [bold green]📅 {date_display}[/bold green]")

            for c in day_data.get("commits", []):
                author_commits += 1
                hash_short = c.get("hash", "")[:8]
                subject = c.get("subject", "")

                ins = sum(f.get("insertions", 0) for f in c.get("files", []))
                dels = sum(f.get("deletions", 0) for f in c.get("files", []))

                author_insertions += ins
                author_deletions += dels

                console.print(
                    f"    [{hash_short}] [bold]{subject}[/bold]"
                )
                pr_note = _format_pull_request_note(c, markdown=False)
                if pr_note:
                    console.print(f"      [cyan]{pr_note}[/cyan]")
                quality_note = _format_quality_note(c)
                if quality_note:
                    console.print(f"      [yellow]{quality_note}[/yellow]")

                # Show file changes
                for f in c.get("files", []):
                    path = f.get("path", "")
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
                        console.print(f"      [dim]├[/dim] {path} ({stats_str})")
                    else:
                        console.print(f"      [dim]├[/dim] {path}")

                # Show commit body if present (short)
                body = c.get("body", "")
                if body and body.strip():
                    # Show first line of body
                    first_line = body.strip().split("\n")[0][:120]
                    if first_line:
                        console.print(f"      [dim]└[/dim] [italic]{first_line}[/italic]")

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
    console.print(text)
    console.print()
