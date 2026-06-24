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
    assert (
        "PR: [#42 Ship \\[unsafe\\]\\(https://evil.test\\)]"
        "(https://github.com/Tresnanda/git-standup/pull/42)" in output
    )
    assert "``src/[bold]auth[/bold]`x`.py`` (+3/-1)" in output


def test_markdown_output_escapes_pr_title_without_url() -> None:
    commit_data = _malicious_commit_data()
    commits = commit_data["[red]Alice[/red]"]["2026-03-10"]["commits"]
    commits[0]["pull_request"] = {
        "number": 42,
        "title": "Ship [unsafe](https://evil.test) and `ticks`",
    }

    output = formatter.build_markdown_output(commit_data)

    assert "PR: #42 Ship \\[unsafe\\]\\(https://evil.test\\) and \\`ticks\\`" in output


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


def test_workflow_board_output_escapes_markdown_controlled_fields() -> None:
    output = formatter.build_workflow_board_output(
        {
            "_repositories": {
                "repo[one]": {
                    "owner`name`": {
                        "2026-03-10": {
                            "commits": [
                                {
                                    "hash": "abc123456789",
                                    "subject": "Fix [link](https://evil.test) and `code`",
                                    "body": "",
                                    "files": [],
                                    "pull_request": {
                                        "number": 42,
                                        "title": "Ship [unsafe](https://evil.test) and `ticks`",
                                        "url": "https://github.com/org/repo name/pull/42)",
                                        "state": "open",
                                        "draft": False,
                                        "review_decision": "approved",
                                        "merge_state_status": "clean",
                                        "updated_at": "2999-01-01T00:00:00Z",
                                        "labels": ["label[one]", "`urgent`"],
                                        "linked_issues": [
                                            {
                                                "number": 7,
                                                "title": "Track [issue](https://evil.test)",
                                                "url": "https://github.com/org/repo/issues/7)",
                                            }
                                        ],
                                    },
                                }
                            ],
                            "stats": {},
                        }
                    }
                }
            }
        }
    )

    assert "owner\\`name\\`" in output
    assert (
        "[#42 Ship \\[unsafe\\]\\(https://evil.test\\) and \\`ticks\\`]"
        "(https://github.com/org/repo%20name/pull/42%29)" in output
    )
    assert " · repo\\[one\\]" in output
    assert "Labels: label\\[one\\], \\`urgent\\`" in output
    assert (
        "[#7 Track \\[issue\\]\\(https://evil.test\\)]"
        "(https://github.com/org/repo/issues/7%29)" in output
    )
    assert "`abc12345` Fix \\[link\\]\\(https://evil.test\\) and \\`code\\`" in output


def test_insights_output_escapes_markdown_controlled_fields() -> None:
    output = formatter.build_insights_output(
        {
            "_repositories": {
                "repo[one]": {
                    "owner`name`": {
                        "2026-03-10": {
                            "commits": [
                                {
                                    "hash": "abc123456789",
                                    "subject": "feat: Add [unsafe](https://evil.test) and `code`",
                                    "body": "",
                                    "files": [
                                        {
                                            "path": "src/[danger]`x`.py",
                                            "insertions": 300,
                                            "deletions": 10,
                                        }
                                    ],
                                    "pull_request": {
                                        "number": 42,
                                        "title": "Ship [unsafe](https://evil.test) and `ticks`",
                                        "url": "https://github.com/org/repo/pull/42",
                                    },
                                }
                            ],
                            "stats": {},
                        }
                    }
                }
            }
        }
    )

    assert "repo\\[one\\]" in output
    assert "owner\\`name\\`" in output
    assert "Add \\[unsafe\\]\\(https://evil.test\\) and \\`code\\`" in output
    assert (
        "Confirm reviewer/merge plan for PR #42 "
        "(Ship \\[unsafe\\]\\(https://evil.test\\) and \\`ticks\\`)." in output
    )
    assert "``src/[danger]`x`.py``" in output


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
