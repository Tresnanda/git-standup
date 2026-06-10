from io import StringIO

import pytest
from rich.console import Console as RichConsole

from git_standup import formatter


def _malicious_commit_data() -> dict[str, object]:
    return {
        "[red]Alice[/red]": {
            "2026-03-10": {
                "commits": [
                    {
                        "hash": "abc123456789",
                        "subject": "Fix [link](https://evil.test) and `code`",
                        "body": "Body with [italic]markup[/italic]",
                        "files": [
                            {
                                "path": "src/[bold]auth[/bold]`x`.py",
                                "insertions": 3,
                                "deletions": 1,
                            }
                        ],
                        "pull_request": {
                            "number": 42,
                            "title": "Ship [unsafe](https://evil.test)",
                            "url": "https://github.com/Tresnanda/git-standup/pull/42",
                        },
                    }
                ],
                "stats": {
                    "total_commits": 1,
                    "total_files": 1,
                    "total_insertions": 3,
                    "total_deletions": 1,
                    "files_changed": ["src/[bold]auth[/bold]`x`.py"],
                },
            }
        }
    }


def test_markdown_output_escapes_subjects_pr_titles_and_code_spans_file_paths() -> None:
    output = formatter.build_markdown_output(_malicious_commit_data())

    assert "## \\[red\\]Alice\\[/red\\]" in output
    assert "Fix \\[link\\]\\(https://evil.test\\) and \\`code\\`" in output
    assert "PR: [#42 Ship \\[unsafe\\]\\(https://evil.test\\)]" in output
    assert "``src/[bold]auth[/bold]`x`.py`` (+3/-1)" in output


def test_changelog_output_escapes_markdown_in_subjects_and_file_paths() -> None:
    output = formatter.build_changelog_output(
        [
            {
                "hash": "def987654321",
                "author_name": "Mallory [x]",
                "author_email": "mallory@example.com",
                "date": "2026-03-10T09:15:00+00:00",
                "subject": "feat(api[core]): add [link](https://evil.test) and `code`",
                "body": "",
                "files": [
                    {
                        "path": "src/[red]danger[/red]`x`.py",
                        "insertions": 5,
                        "deletions": 0,
                    }
                ],
            }
        ]
    )

    assert "**api\\[core\\]:** add \\[link\\]\\(https://evil.test\\) and \\`code\\`" in output
    assert "``src/[red]danger[/red]`x`.py`` (+5/-0)" in output
    assert "Mallory \\[x\\]" in output


def test_rich_terminal_output_escapes_git_controlled_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StringIO()

    def fake_console(*_args: object, **_kwargs: object) -> RichConsole:
        return RichConsole(file=stream, force_terminal=True, color_system=None, width=120)

    monkeypatch.setattr(formatter, "Console", fake_console)

    formatter.print_text_standup(_malicious_commit_data())
    formatter.print_ai_standup("AI copied [bold]markup[/bold] from a commit")

    output = stream.getvalue()
    assert "[red]Alice[/red]" in output
    assert "Fix [link](https://evil.test) and `code`" in output
    assert "src/[bold]auth[/bold]`x`.py" in output
    assert "Body with [italic]markup[/italic]" in output
    assert "AI copied [bold]markup[/bold] from a commit" in output
