"""CLI entry point for git-standup."""

import argparse
import sys
from typing import Any

from git_standup import __version__
from git_standup.ai import generate_standup
from git_standup.formatter import (
    build_json_output,
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="git-standup",
        description="AI-powered weekly standup generator. Analyze git history and "
        "generate standup summaries.",
        epilog="Examples:\n"
        "  git-standup                     # Last 7 days, all contributors\n"
        "  git-standup --days 1            # Yesterday only\n"
        "  git-standup --author me         # My commits only\n"
        "  git-standup --no-ai             # Text summary without LLM\n"
        "  git-standup --json              # Raw JSON output\n"
        "  git-standup --api-key sk-...    # Custom API key\n"
        "  git-standup --model gpt-4       # Custom model\n"
        "  git-standup --base-url https://api.openai.com/v1  # Custom endpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of git history to include (default: 7)",
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

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    try:
        commits = get_commits(
            days=args.days,
            author=args.author,
            base_branch=args.base_branch,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not commits:
        print("No commits found in the specified time range.")
        return 0

    commit_data = _build_commit_data(commits)

    if args.json:
        print(build_json_output(commit_data))
        return 0

    if args.no_ai:
        print_text_standup(commit_data)
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
        print_text_standup(commit_data)
        return 1

    print_ai_standup(standup_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
