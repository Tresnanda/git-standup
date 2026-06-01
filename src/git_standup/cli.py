"""CLI entry point for git-standup."""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from git_standup import __version__
from git_standup.ai import generate_standup
from git_standup.formatter import (
    build_json_output,
    build_markdown_output,
    build_text_output,
    print_ai_standup,
    print_text_standup,
)
from git_standup.gitlog import (
    compute_stats,
    get_commits,
    group_by_author,
    group_by_date,
)


def _build_commit_data(
    commits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured data dict grouped by author -> date -> commits."""
    by_author = group_by_author(commits)
    result: dict[str, Any] = {}

    for author, author_commits in by_author.items():
        by_date = group_by_date(author_commits)
        date_data: dict[str, Any] = {}
        for date_key, day_commits in by_date.items():
            stats = compute_stats(day_commits)
            date_data[date_key] = {
                "commits": [
                    {
                        "hash": c.get("hash", ""),
                        "subject": c.get("subject", ""),
                        "body": c.get("body", ""),
                        "files": c.get("files", []),
                    }
                    for c in day_commits
                ],
                "stats": stats,
            }
        result[author] = date_data

    return result


def _positive_int(value: str) -> int:
    """Parse a positive integer for CLI arguments."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _date_string(value: str) -> str:
    """Parse an ISO date string for exact report windows."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD")
    return value


def _write_output(text: str, output_path: str | None) -> bool:
    """Write text to output_path when provided. Return True when handled."""
    if output_path is None:
        return False
    Path(output_path).write_text(text, encoding="utf-8")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="git-standup",
        description="AI-powered weekly standup generator. Analyze git history and "
        "generate standup summaries.",
        epilog="Examples:\n"
        "  git-standup                     # Last 7 days, all contributors\n"
        "  git-standup me                  # My commits, no AI required\n"
        "  git-standup branch              # Current branch vs main, no AI required\n"
        "  git-standup ../api --markdown   # Run against another repository\n"
        "  git-standup --days 1            # Yesterday only\n"
        "  git-standup --repo ../api       # Run against another repository\n"
        "  git-standup --since 2026-01-01 --until 2026-01-07\n"
        "  git-standup --author me         # My commits only\n"
        "  git-standup --no-ai             # Text summary without LLM\n"
        "  git-standup --markdown          # Markdown summary without LLM\n"
        "  git-standup --json              # Raw JSON output\n"
        "  git-standup --markdown --output standup.md\n"
        "  git-standup --api-key sk-...    # Custom API key\n"
        "  git-standup --model gpt-4       # Custom model\n"
        "  git-standup --base-url https://api.openai.com/v1  # Custom endpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "target",
        nargs="?",
        help="Optional preset (me, week, branch) or repository path",
    )
    parser.add_argument(
        "--days",
        type=_positive_int,
        default=7,
        help="Number of days of git history to include (default: 7)",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Path to the git repository to analyze (default: current directory)",
    )
    parser.add_argument(
        "--since",
        type=_date_string,
        default=None,
        help="Start date for the report window (YYYY-MM-DD). Overrides --days.",
    )
    parser.add_argument(
        "--until",
        type=_date_string,
        default=None,
        help="End date for the report window (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Filter commits by author. Use 'me' for the current git user.",
    )
    parser.add_argument(
        "--base-branch",
        type=str,
        default=None,
        help="Base branch for comparing changes (e.g., 'main'). Shows commits "
        "in current branch not in base.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON (no AI processing)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Output formatted text summary without AI",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output a paste-ready Markdown summary without AI",
    )
    parser.add_argument(
        "--output", "--out",
        type=str,
        default=None,
        help="Write the generated JSON, Markdown, text, or AI summary to a file",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key for the LLM provider (default: OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="Model name to use (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Base URL for OpenAI-compatible API (default: https://api.openai.com/v1)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-standup {__version__}",
    )

    args = parser.parse_args(argv)
    if args.target:
        if args.target == "me":
            args.author = "me"
            args.no_ai = True
        elif args.target == "week":
            args.days = 7
            args.no_ai = True
        elif args.target == "branch":
            if args.base_branch is None:
                args.base_branch = "main"
            args.no_ai = True
        else:
            if args.repo is not None:
                parser.error(
                    "provide a repository path either positionally or with --repo, not both"
                )
            args.repo = args.target
    del args.target
    return args


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    try:
        commits = get_commits(
            days=args.days,
            author=args.author,
            base_branch=args.base_branch,
            repo_path=args.repo,
            since=args.since,
            until=args.until,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not commits:
        print("No commits found in the specified time range.")
        return 0

    commit_data = _build_commit_data(commits)

    if args.json:
        output = build_json_output(commit_data)
        if not _write_output(output + "\n", args.output):
            print(output)
        return 0

    if args.no_ai:
        if not _write_output(build_text_output(commit_data), args.output):
            print_text_standup(commit_data)
        return 0

    if args.markdown:
        output = build_markdown_output(commit_data)
        if not _write_output(output, args.output):
            print(output, end="")
        return 0

    # AI mode
    try:
        standup_text = generate_standup(
            commit_data=commit_data,
            api_key=args.api_key,
            model=args.model,
            base_url=args.base_url,
        )
    except RuntimeError as exc:
        # Fall back to text summary if AI fails
        print(
            f"Warning: AI generation failed ({exc}). Showing text summary instead.\n",
            file=sys.stderr,
        )
        if not _write_output(build_text_output(commit_data), args.output):
            print_text_standup(commit_data)
        return 1

    if not _write_output(standup_text.rstrip() + "\n", args.output):
        print_ai_standup(standup_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
