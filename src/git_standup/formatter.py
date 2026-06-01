"""Output formatting — pretty printing with Rich and JSON serialization."""

import json
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


def build_json_output(
    commit_data: dict[str, Any],
) -> str:
    """Serialize commit data to JSON."""
    return json.dumps(commit_data, indent=2, default=str)


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
        console.print(f"[dim]─[/dim]" * 50)

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
