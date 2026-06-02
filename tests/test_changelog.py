from git_standup.formatter import build_changelog_output


def _commit(
    subject: str,
    *,
    hash_: str = "abc123456789",
    body: str = "",
    author: str = "Alice",
    files: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "hash": hash_,
        "author_name": author,
        "author_email": f"{author.lower()}@example.com",
        "date": "2026-03-10T09:15:00+00:00",
        "subject": subject,
        "body": body,
        "files": files or [{"path": "src/app.py", "insertions": 12, "deletions": 2}],
    }


def test_changelog_groups_conventional_commit_categories() -> None:
    output = build_changelog_output(
        [
            _commit("feat(api): add login tokens", hash_="fea11111"),
            _commit("fix: handle expired sessions", hash_="fix22222"),
            _commit(
                "docs: explain setup",
                hash_="doc33333",
                files=[{"path": "README.md", "insertions": 5, "deletions": 0}],
            ),
            _commit("refactor(auth): split token parser", hash_="ref44444"),
            _commit("chore: update dependencies", hash_="cho55555"),
            _commit("ship experimental dashboard", hash_="oth66666"),
        ]
    )

    assert output.startswith("# Changelog\n")
    assert "## Features\n\n- **api:** add login tokens (`fea11111`)" in output
    assert "## Fixes\n\n- handle expired sessions (`fix22222`)" in output
    assert "## Docs\n\n- explain setup (`doc33333`)" in output
    assert "## Refactors\n\n- **auth:** split token parser (`ref44444`)" in output
    assert "## Chores\n\n- update dependencies (`cho55555`)" in output
    assert "## Other\n\n- ship experimental dashboard (`oth66666`)" in output
    assert "## Change Stats" in output
    assert "- Total: 6 commit(s), 2 file(s), +65/-10 lines" in output


def test_changelog_marks_breaking_changes_and_truncated_file_lists() -> None:
    commit = _commit(
        "feat!: replace auth token format",
        hash_="abc98765",
        files=[
            {"path": "src/auth.py", "insertions": 30, "deletions": 5},
            {"path": "tests/test_auth.py", "insertions": 10, "deletions": 1},
        ],
    )
    commit["truncated"] = {"files": True, "files_omitted": 4}

    output = build_changelog_output(
        [commit],
        {
            "truncated": True,
            "limits": {"max_commits": 1, "max_files_per_commit": 2},
            "commits_truncated": True,
            "files_truncated": True,
            "files_omitted": 4,
        },
    )

    assert "_Note: output was truncated" in output
    assert "⚠️ replace auth token format (`abc98765`) — 2 file(s), +40/-6 lines" in output
    assert "Files omitted by `--max-files-per-commit`: 4" in output
