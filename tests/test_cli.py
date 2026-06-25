import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_standup import cli, github_api
from git_standup.author_aliases import AuthorAliases
from git_standup.formatter import build_markdown_output, build_stats_output


@pytest.fixture(autouse=True)
def isolated_user_ai_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(cli, "_generated_timestamp", lambda: "2026-06-06T12:00:00Z")
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
        "TOGETHER_API_KEY",
        "PERPLEXITY_API_KEY",
        "XAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "CURSOR_API_KEY",
        "KIRO_API_KEY",
        "AMP_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _sample_commits() -> list[dict[str, object]]:
    return [
        {
            "hash": "abc123",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T09:15:00+00:00",
            "subject": "Add authentication",
            "body": "",
            "files": [{"path": "src/auth.py", "insertions": 12, "deletions": 2}],
        }
    ]


def _sample_commit_data(subject: str) -> dict[str, object]:
    commits = _sample_commits()
    commits[0]["subject"] = subject
    return cli._build_commit_data(commits)


class _TempDir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *_args: object) -> None:
        return None


def _default_json_metadata(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "generated_at": "2026-06-06T12:00:00Z",
        "query_window": {"days": 7, "since": None, "until": None},
        "author": None,
        "base_branch": None,
        "exclude_merges": False,
        "include_prs": False,
        "pathspecs": [],
        "repository": {"type": "local", "path": "."},
    }
    metadata.update(overrides)
    return metadata


def test_json_mode_prints_structured_commit_data(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    exit_code = cli.main(["--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["Alice"]["2026-03-10"]["stats"]["total_commits"] == 1
    assert output["Alice"]["2026-03-10"]["commits"][0]["subject"] == "Add authentication"
    assert output["_metadata"] == _default_json_metadata()


def test_author_alias_cli_merges_json_author_groups(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    commits = _sample_commits() + [
        {
            "hash": "def456",
            "author_name": "Alice E.",
            "author_email": "alice@users.noreply.github.com",
            "date": "2026-03-10T10:15:00+00:00",
            "subject": "Fix alias report",
            "body": "",
            "files": [{"path": "src/report.py", "insertions": 3, "deletions": 1}],
        }
    ]
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return commits

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(
        [
            "--json",
            "--author-alias",
            "Alice=alice@example.com,Alice E.,alice@users.noreply.github.com",
        ]
    )

    assert exit_code == 0
    aliases = captured["author_aliases"]
    assert isinstance(aliases, AuthorAliases)
    assert aliases.expand_filter("Alice") == (
        "Alice|alice@example.com|Alice E.|alice@users.noreply.github.com"
    )
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"_metadata", "Alice"}
    assert output["_metadata"] == _default_json_metadata()
    assert output["Alice"]["2026-03-10"]["stats"]["total_commits"] == 2
    assert [
        commit["subject"] for commit in output["Alice"]["2026-03-10"]["commits"]
    ] == ["Add authentication", "Fix alias report"]


def test_author_alias_config_merges_team_digest_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[author_aliases]\n"Alice" = ["Alice E.", "alice@users.noreply.github.com"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "config_path", lambda: config_path)
    commits = _sample_commits() + [
        {
            "hash": "def456",
            "author_name": "Alice E.",
            "author_email": "alice@users.noreply.github.com",
            "date": "2026-03-10T10:15:00+00:00",
            "subject": "Fix alias digest",
            "body": "",
            "files": [{"path": "src/report.py", "insertions": 3, "deletions": 1}],
        }
    ]
    monkeypatch.setattr(cli, "get_commits", lambda **_: commits)

    exit_code = cli.main(["--team-digest"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.count("## Owner: Alice") == 1
    assert "## Owner: Alice E." not in output
    assert "- Commits: 2 · Files: 2 · Lines: +15/-3" in output


def test_include_prs_enriches_json_output_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_enrich(commits: list[dict[str, object]], **kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        enriched = [dict(commit) for commit in commits]
        enriched[0]["pull_request"] = {
            "number": 42,
            "title": "Add PR-aware digest",
            "url": "https://github.com/Tresnanda/git-standup/pull/42",
            "source": "github-cli",
        }
        return enriched

    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    monkeypatch.setattr(cli, "enrich_commits_with_prs", fake_enrich)

    exit_code = cli.main(["--json", "--include-prs"])

    assert exit_code == 0
    assert captured == {"repo_path": None, "query_github": True}
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"] == _default_json_metadata(include_prs=True)
    pr = output["Alice"]["2026-03-10"]["commits"][0]["pull_request"]
    assert pr["number"] == 42
    assert pr["title"] == "Add PR-aware digest"


def test_default_json_output_does_not_attempt_pr_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    monkeypatch.setattr(
        cli,
        "enrich_commits_with_prs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected PR lookup")),
    )

    assert cli.main(["--json"]) == 0


def test_json_mode_includes_budget_metadata_when_limited(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    commits = [
        {
            "hash": "abc123",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T09:15:00+00:00",
            "subject": "Add authentication",
            "body": "",
            "files": [
                {"path": "src/auth.py", "insertions": 12, "deletions": 2},
                {"path": "tests/test_auth.py", "insertions": 4, "deletions": 0},
            ],
        },
        {
            "hash": "def456",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T10:15:00+00:00",
            "subject": "Fix login redirect",
            "body": "",
            "files": [{"path": "src/login.py", "insertions": 3, "deletions": 1}],
        },
        {
            "hash": "ghi789",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T11:15:00+00:00",
            "subject": "Document auth",
            "body": "",
            "files": [{"path": "README.md", "insertions": 2, "deletions": 0}],
        },
    ]

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return commits

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(["--json", "--max-commits", "2", "--max-files-per-commit", "1"])

    assert exit_code == 0
    assert captured["max_commits"] == 3
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"] == {
        **_default_json_metadata(),
        "truncated": True,
        "limits": {"max_commits": 2, "max_files_per_commit": 1},
        "commits_included": 2,
        "commits_truncated": True,
        "more_commits_available": True,
        "files_truncated": True,
        "commits_with_files_truncated": 1,
        "files_omitted": 1,
    }
    output_commits = output["Alice"]["2026-03-10"]["commits"]
    assert [commit["hash"] for commit in output_commits] == ["abc123", "def456"]
    assert output_commits[0]["files"] == [
        {"path": "src/auth.py", "insertions": 12, "deletions": 2}
    ]
    assert output_commits[0]["truncated"] == {"files": True, "files_omitted": 1}


def test_json_mode_includes_pathspec_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return _sample_commits()

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(["--json", "--path", "src", "--pathspec", "tests"])

    assert exit_code == 0
    assert captured["pathspecs"] == ["src", "tests"]
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"] == _default_json_metadata(pathspecs=["src", "tests"])


def test_json_mode_groups_multiple_remote_repositories(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    tmp_path,
) -> None:
    cloned: list[tuple[str, str]] = []

    def fake_clone(remote: str, parent: Path) -> Path:
        path = parent / remote.replace("/", "__")
        cloned.append((remote, str(path)))
        return path

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        commits = _sample_commits()
        commits[0]["subject"] = f"Report {Path(str(kwargs['repo_path'])).name}"
        return commits

    monkeypatch.setattr(cli, "_clone_remote_repo", fake_clone)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", lambda: _TempDir(tmp_path))
    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-repo",
            "Tresnanda/web",
            "--json",
        ]
    )

    assert exit_code == 0
    assert cloned == [
        ("Tresnanda/api", str(tmp_path / "Tresnanda__api")),
        ("Tresnanda/web", str(tmp_path / "Tresnanda__web")),
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"] == _default_json_metadata(
        repository={
            "type": "remote",
            "repositories": ["Tresnanda/api", "Tresnanda/web"],
            "backend": "clone",
        }
    )
    assert set(output["_repositories"]) == {"Tresnanda/api", "Tresnanda/web"}
    assert output["_repositories"]["Tresnanda/api"]["Alice"]["2026-03-10"]["commits"][0][
        "subject"
    ] == "Report Tresnanda__api"


def _budgeted_remote_commits(repo_path: object) -> list[dict[str, object]]:
    repo_name = Path(str(repo_path)).name
    return [
        {
            "hash": f"{repo_name}-1",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T09:15:00+00:00",
            "subject": f"Report {repo_name} first",
            "body": "",
            "files": [
                {"path": f"{repo_name}/app.py", "insertions": 12, "deletions": 2},
                {"path": f"{repo_name}/test_app.py", "insertions": 4, "deletions": 0},
            ],
        },
        {
            "hash": f"{repo_name}-2",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T10:15:00+00:00",
            "subject": f"Report {repo_name} second",
            "body": "",
            "files": [{"path": f"{repo_name}/docs.md", "insertions": 2, "deletions": 0}],
        },
    ]


def _budgeted_api_commits(repo_name: str) -> list[dict[str, object]]:
    return [
        {
            "hash": f"{repo_name}-1",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T09:15:00+00:00",
            "subject": f"Report {repo_name} first",
            "body": "",
            "files": [
                {"path": f"{repo_name}/app.py", "insertions": 12, "deletions": 2},
                {"path": f"{repo_name}/test_app.py", "insertions": 4, "deletions": 0},
            ],
        },
        {
            "hash": f"{repo_name}-2",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2026-03-10T10:15:00+00:00",
            "subject": f"Report {repo_name} second",
            "body": "",
            "files": [{"path": f"{repo_name}/docs.md", "insertions": 2, "deletions": 0}],
        },
    ]


def test_multi_remote_json_includes_per_repository_budget_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    tmp_path,
) -> None:
    def fake_clone(remote: str, parent: Path) -> Path:
        return parent / remote.replace("/", "__")

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        return _budgeted_remote_commits(kwargs["repo_path"])

    monkeypatch.setattr(cli, "_clone_remote_repo", fake_clone)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", lambda: _TempDir(tmp_path))
    monkeypatch.setattr(cli, "get_commits", fake_get_commits)
    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-repo",
            "Tresnanda/web",
            "--max-commits",
            "1",
            "--max-files-per-commit",
            "1",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"]["repositories"] == {
        "Tresnanda/api": {
            "truncated": True,
            "limits": {"max_commits": 1, "max_files_per_commit": 1},
            "commits_included": 1,
            "commits_truncated": True,
            "more_commits_available": True,
            "files_truncated": True,
            "commits_with_files_truncated": 1,
            "files_omitted": 1,
        },
        "Tresnanda/web": {
            "truncated": True,
            "limits": {"max_commits": 1, "max_files_per_commit": 1},
            "commits_included": 1,
            "commits_truncated": True,
            "more_commits_available": True,
            "files_truncated": True,
            "commits_with_files_truncated": 1,
            "files_omitted": 1,
        },
    }
    api_commits = output["_repositories"]["Tresnanda/api"]["Alice"]["2026-03-10"]["commits"]
    assert [commit["hash"] for commit in api_commits] == ["Tresnanda__api-1"]
    assert api_commits[0]["files"] == [
        {"path": "Tresnanda__api/app.py", "insertions": 12, "deletions": 2}
    ]
    assert api_commits[0]["truncated"] == {"files": True, "files_omitted": 1}


def test_multi_remote_ai_receives_per_repository_budget_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    def fake_clone(remote: str, parent: Path) -> Path:
        return parent / remote.replace("/", "__")

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        return _budgeted_remote_commits(kwargs["repo_path"])

    def fake_generation(**kwargs: object) -> str:
        captured.update(kwargs)
        return "AI standup"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_clone_remote_repo", fake_clone)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", lambda: _TempDir(tmp_path))
    monkeypatch.setattr(cli, "get_commits", fake_get_commits)
    monkeypatch.setattr(cli, "generate_standup", fake_generation)

    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-repo",
            "Tresnanda/web",
            "--max-commits",
            "1",
            "--max-files-per-commit",
            "1",
        ]
    )

    assert exit_code == 0
    assert captured["budget_metadata"] == {
        "repositories": {
            "Tresnanda/api": {
                "truncated": True,
                "limits": {"max_commits": 1, "max_files_per_commit": 1},
                "commits_included": 1,
                "commits_truncated": True,
                "more_commits_available": True,
                "files_truncated": True,
                "commits_with_files_truncated": 1,
                "files_omitted": 1,
            },
            "Tresnanda/web": {
                "truncated": True,
                "limits": {"max_commits": 1, "max_files_per_commit": 1},
                "commits_included": 1,
                "commits_truncated": True,
                "more_commits_available": True,
                "files_truncated": True,
                "commits_with_files_truncated": 1,
                "files_omitted": 1,
            },
        }
    }
    commit_data = captured["commit_data"]
    assert isinstance(commit_data, dict)
    assert set(commit_data["_repositories"]) == {"Tresnanda/api", "Tresnanda/web"}


def test_multi_remote_api_json_includes_per_repository_budget_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    def fail_clone(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("API backend must not clone remote repositories")

    def fake_get_remote_commits(repo: str, **_kwargs: object) -> list[dict[str, object]]:
        return _budgeted_api_commits(repo)

    monkeypatch.setattr(cli, "_clone_remote_repo", fail_clone)
    monkeypatch.setattr(cli, "get_remote_commits", fake_get_remote_commits)

    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-backend",
            "api",
            "--max-commits",
            "1",
            "--max-files-per-commit",
            "1",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"]["repositories"]["Tresnanda/api"] == {
        "truncated": True,
        "limits": {"max_commits": 1, "max_files_per_commit": 1},
        "commits_included": 1,
        "commits_truncated": True,
        "more_commits_available": True,
        "files_truncated": True,
        "commits_with_files_truncated": 1,
        "files_omitted": 1,
    }
    commits = output["_repositories"]["Tresnanda/api"]["Alice"]["2026-03-10"]["commits"]
    assert [commit["hash"] for commit in commits] == ["Tresnanda/api-1"]


def test_github_api_author_filter_treats_alias_email_literals() -> None:
    item = {
        "commit": {
            "author": {
                "name": "Alice",
                "email": "123+alice@users.noreply.github.com",
            }
        },
        "author": {"login": "alice-gh"},
    }

    assert github_api._matches_author(item, "123+alice@users.noreply.github.com")
    assert not github_api._matches_author(item, "1234alice@usersxnoreplyxgithubxcom")


def test_remote_api_backend_uses_github_api_without_cloning(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    api_calls: list[tuple[str, dict[str, str | int], list[str]]] = []

    def fail_clone(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("API backend must not clone remote repositories")

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        api_calls.append((endpoint, params or {}, headers or []))
        if endpoint == "/repos/Tresnanda/api/commits":
            assert params is not None
            assert params["since"] == "2026-03-01T00:00:00Z"
            assert params["until"] == "2026-03-11T23:59:59Z"
            return [
                {
                    "sha": "abcdef1234567890",
                    "parents": [{"sha": "parent"}],
                    "author": {"login": "alicehub"},
                    "commit": {
                        "author": {
                            "name": "Alice",
                            "email": "alice@example.com",
                            "date": "2026-03-10T09:15:00Z",
                        },
                        "message": "Add API backend\n\nFetch commits without cloning.",
                    },
                },
                {
                    "sha": "merge123",
                    "parents": [{"sha": "p1"}, {"sha": "p2"}],
                    "commit": {
                        "author": {
                            "name": "Alice",
                            "email": "alice@example.com",
                            "date": "2026-03-10T10:15:00Z",
                        },
                        "message": "Merge pull request #99",
                    },
                },
            ]
        if endpoint == "/repos/Tresnanda/api/commits/abcdef1234567890":
            return {
                "sha": "abcdef1234567890",
                "author": {"login": "alicehub"},
                "commit": {
                    "author": {
                        "name": "Alice",
                        "email": "alice@example.com",
                        "date": "2026-03-10T09:15:00Z",
                    },
                    "message": "Add API backend\n\nFetch commits without cloning.",
                },
                "files": [
                    {"filename": "src/github_api.py", "additions": 40, "deletions": 1},
                    {"filename": "tests/test_cli.py", "additions": 20, "deletions": 0},
                ],
            }
        if endpoint == "/repos/Tresnanda/api/commits/abcdef1234567890/pulls":
            assert "Accept: application/vnd.github.groot-preview+json" in (headers or [])
            return [
                {
                    "number": 42,
                    "title": "Add no-clone API backend",
                    "html_url": "https://github.com/Tresnanda/api/pull/42",
                }
            ]
        raise AssertionError(f"unexpected API endpoint: {endpoint}")

    monkeypatch.setattr(cli, "_clone_remote_repo", fail_clone)
    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)

    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-backend",
            "api",
            "--json",
            "--since",
            "2026-03-01",
            "--until",
            "2026-03-11",
            "--author",
            "Alice",
            "--exclude-merges",
            "--max-commits",
            "1",
            "--include-prs",
        ]
    )

    assert exit_code == 0
    assert [call[0] for call in api_calls] == [
        "/repos/Tresnanda/api/commits",
        "/repos/Tresnanda/api/commits/abcdef1234567890",
        "/repos/Tresnanda/api/commits/abcdef1234567890/pulls",
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"]["query_window"] == {
        "days": 7,
        "since": "2026-03-01",
        "until": "2026-03-11",
    }
    assert output["_metadata"]["author"] == "Alice"
    assert output["_metadata"]["exclude_merges"] is True
    assert output["_metadata"]["include_prs"] is True
    assert output["_metadata"]["repository"] == {
        "type": "remote",
        "repositories": ["Tresnanda/api"],
        "backend": "api",
    }
    repo_output = output["_repositories"]["Tresnanda/api"]
    commit = repo_output["Alice"]["2026-03-10"]["commits"][0]
    assert commit["hash"] == "abcdef1234567890"
    assert commit["subject"] == "Add API backend"
    assert commit["body"] == "Fetch commits without cloning."
    assert commit["files"] == [
        {"path": "src/github_api.py", "insertions": 40, "deletions": 1},
        {"path": "tests/test_cli.py", "insertions": 20, "deletions": 0},
    ]
    assert commit["pull_request"] == {
        "number": 42,
        "source": "github-api",
        "title": "Add no-clone API backend",
        "url": "https://github.com/Tresnanda/api/pull/42",
    }
    assert repo_output["Alice"]["2026-03-10"]["stats"] == {
        "total_commits": 1,
        "total_insertions": 60,
        "total_deletions": 1,
        "total_files": 2,
        "files_changed": ["src/github_api.py", "tests/test_cli.py"],
    }


def test_remote_api_backend_rejects_git_native_filters(capsys) -> None:
    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-backend",
            "api",
            "--path",
            "src",
            "--json",
        ]
    )

    assert exit_code == 1
    assert "--remote-backend api does not support --path/--pathspec" in capsys.readouterr().err


def test_markdown_mode_prints_paste_ready_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    exit_code = cli.main(["--markdown", "--no-ai"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "# Standup Summary" in output
    assert "## Alice" in output
    assert "- `abc123` Add authentication" in output


def test_commit_data_marks_low_signal_subjects() -> None:
    commits = _sample_commits()
    commits[0]["subject"] = "wip"

    data = cli._build_commit_data(commits)
    commit = data["Alice"]["2026-03-10"]["commits"][0]

    assert commit["quality"]["signal"] == "low"
    assert "generic subject `wip`" in commit["quality"]["reasons"]
    assert "do not embellish" in commit["quality"]["guidance"]


def test_markdown_output_notes_low_signal_commit_messages() -> None:
    data = _sample_commit_data("wip")

    output = build_markdown_output(data)

    assert "- `abc123` wip" in output
    assert "Low-signal commit message" in output
    assert "generic subject \\`wip\\`" in output


def test_markdown_output_includes_pull_request_metadata() -> None:
    commits = _sample_commits()
    commits[0]["pull_request"] = {
        "number": 42,
        "title": "Add PR-aware digest",
        "url": "https://github.com/Tresnanda/git-standup/pull/42",
    }

    output = build_markdown_output(cli._build_commit_data(commits))

    assert "- `abc123` Add authentication" in output
    pr_link = "PR: [#42 Add PR-aware digest](https://github.com/Tresnanda/git-standup/pull/42)"
    assert pr_link in output


def test_markdown_mode_formats_multiple_repositories() -> None:
    output = build_markdown_output(
        {
            "_repositories": {
                "Tresnanda/api": _sample_commit_data("Add API"),
                "Tresnanda/web": _sample_commit_data("Add web UI"),
            }
        }
    )

    assert "## Tresnanda/api" in output
    assert "### Alice" in output
    assert "#### 2026-03-10" in output
    assert "- `abc123` Add API" in output
    assert "## Tresnanda/web" in output
    assert "- `abc123` Add web UI" in output


def test_stats_only_mode_prints_aggregate_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    exit_code = cli.main(["--stats-only"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Standup Stats" in output
    assert "Alice" in output
    assert "2026-03-10: 1 commit(s), 1 file(s), +12/-2 lines" in output
    assert "Total: 1 commit(s), 1 file(s), +12/-2 lines" in output
    assert "Add authentication" not in output


def test_stats_output_formats_multiple_repositories_as_markdown() -> None:
    output = build_stats_output(
        {
            "_repositories": {
                "Tresnanda/api": _sample_commit_data("Add API"),
                "Tresnanda/web": _sample_commit_data("Add web UI"),
            }
        },
        output_format="markdown",
    )

    assert "# Standup Stats" in output
    assert "## Tresnanda/api" in output
    assert "### Alice" in output
    assert "- 2026-03-10: 1 commit(s), 1 file(s), +12/-2 lines" in output
    assert "## Tresnanda/web" in output
    assert "## Summary" in output
    assert "- **Total: 2 commit(s), 2 file(s), +24/-4 lines**" in output


def test_markdown_mode_notes_commit_budget_truncation(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits() + _sample_commits())

    exit_code = cli.main(["--markdown", "--no-ai", "--max-commits", "1"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Note: output was truncated" in output
    assert "commit list limited to 1 commit(s)" in output


def test_text_mode_notes_file_budget_truncation(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    commits = _sample_commits()
    commits[0]["files"] = [
        {"path": "src/auth.py", "insertions": 12, "deletions": 2},
        {"path": "src/login.py", "insertions": 3, "deletions": 1},
    ]
    monkeypatch.setattr(cli, "get_commits", lambda **_: commits)

    exit_code = cli.main(["--no-ai", "--max-files-per-commit", "1"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Note: output was truncated - file lists limited to 1 file(s) per commit" in output


def test_stats_only_mode_notes_commit_budget_truncation(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits() + _sample_commits())

    exit_code = cli.main(["--stats-only", "--max-commits", "1"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Note: output was truncated" in output
    assert "commit list limited to 1 commit(s)" in output


def test_changelog_mode_prints_release_note_markdown_without_ai(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    commits = _sample_commits()
    commits[0]["subject"] = "feat(auth): add authentication"
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return commits

    def fail_if_called(**_: object) -> str:
        raise AssertionError("AI should not be called in changelog mode")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "get_commits", fake_get_commits)
    monkeypatch.setattr(cli, "generate_standup", fail_if_called)

    exit_code = cli.main(
        [
            "--changelog",
            "--since",
            "2026-01-01",
            "--until",
            "2026-01-07",
            "--author",
            "Alice",
            "--max-commits",
            "5",
            "--max-files-per-commit",
            "2",
        ]
    )

    assert exit_code == 0
    assert captured["since"] == "2026-01-01"
    assert captured["until"] == "2026-01-07"
    assert captured["author"] == "Alice"
    assert captured["max_commits"] == 6
    output = capsys.readouterr().out
    assert "# Changelog" in output
    assert "## Features" in output
    assert "- **auth:** add authentication (`abc123`)" in output
    assert "## Change Stats" in output


def test_changelog_mode_writes_to_output_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    commits = _sample_commits()
    commits[0]["subject"] = "fix: repair login redirect"
    monkeypatch.setattr(cli, "get_commits", lambda **_: commits)
    output_path = tmp_path / "changelog.md"

    exit_code = cli.main(["--changelog", "--output", str(output_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    output = output_path.read_text(encoding="utf-8")
    assert "# Changelog" in output
    assert "## Fixes" in output
    assert "repair login redirect" in output


def test_changelog_mode_notes_low_signal_commit_messages(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    commits = _sample_commits()
    commits[0]["subject"] = "fix"
    monkeypatch.setattr(cli, "get_commits", lambda **_: commits)

    exit_code = cli.main(["--changelog"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Low-signal commit message" in output
    assert "generic subject \\`fix\\`" in output


def test_main_passes_repo_and_exact_dates_to_gitlog(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return _sample_commits()

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(
        [
            "--repo",
            "/workspace/app",
            "--since",
            "2026-01-01",
            "--until",
            "2026-01-07",
            "--author",
            "Alice",
            "--base-branch",
            "main",
            "--path",
            "src",
            "--exclude-merges",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["repo_path"] == "/workspace/app"
    assert captured["since"] == "2026-01-01"
    assert captured["until"] == "2026-01-07"
    assert captured["author"] == "Alice"
    assert captured["base_branch"] == "main"
    assert captured["pathspecs"] == ["src"]
    assert captured["exclude_merges"] is True
    output = json.loads(capsys.readouterr().out)
    assert output["_metadata"] == _default_json_metadata(
        query_window={"days": 7, "since": "2026-01-01", "until": "2026-01-07"},
        author="Alice",
        base_branch="main",
        exclude_merges=True,
        pathspecs=["src"],
        repository={"type": "local", "path": "/workspace/app"},
    )


def test_parse_args_supports_easy_presets_and_positional_repo() -> None:
    default = cli.parse_args([])
    assert default.exclude_merges is False
    assert default.remote_backend == "clone"
    assert default.since_last is False
    assert default.write_checkpoint is False

    me = cli.parse_args(["me"])
    assert me.author == "me"
    assert me.no_ai is True

    no_merges = cli.parse_args(["--exclude-merges"])
    assert no_merges.exclude_merges is True

    pr_digest = cli.parse_args(["--pr-digest"])
    assert pr_digest.include_prs is True

    branch = cli.parse_args(["branch"])
    assert branch.base_branch == "main"
    assert branch.no_ai is True

    repo = cli.parse_args(["../api", "--markdown", "--out", "standup.md"])
    assert repo.repo == "../api"
    assert repo.markdown is True
    assert repo.output == "standup.md"

    changelog = cli.parse_args(["--changelog"])
    assert changelog.changelog is True

    insights = cli.parse_args(["--insights"])
    assert insights.insights is True

    stats = cli.parse_args(["--stats-only"])
    assert stats.stats_only is True

    paths = cli.parse_args(["--path", "src", "--pathspec", "README.md"])
    assert paths.pathspecs == ["src", "README.md"]

    aliases = cli.parse_args(["--author-alias", "Alice=alice@example.com,Alice E."])
    assert aliases.author_alias == ["Alice=alice@example.com,Alice E."]

    checkpoint = cli.parse_args(["--since-last", "--write-checkpoint"])
    assert checkpoint.since_last is True
    assert checkpoint.write_checkpoint is True


def test_since_last_cannot_be_combined_with_since() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--since-last", "--since", "2026-06-01"])


def test_since_last_uses_checkpoint_and_can_write_next_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    checkpoint_file = tmp_path / "checkpoints.json"
    repo_root = str(tmp_path / "repo")
    repo_id = cli.local_repository_id(repo_root)
    checkpoint_file.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {
                    repo_id: {
                        "since": "2026-06-08 09:00:00 +0000",
                        "label": repo_root,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "checkpoint_path", lambda: checkpoint_file)
    monkeypatch.setattr(cli, "get_repo_root", lambda repo_path=None: repo_root)
    monkeypatch.setattr(
        cli,
        "_checkpoint_timestamp",
        lambda now=None: "2026-06-08 17:30:00 +0000",
    )

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return _sample_commits()

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(["--since-last", "--write-checkpoint", "--no-ai"])

    assert exit_code == 0
    assert captured["since"] == "2026-06-08 09:00:00 +0000"
    assert captured["until"] == "2026-06-08 17:30:00 +0000"
    data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert data["repositories"][repo_id]["since"] == "2026-06-08 17:30:00 +0000"
    assert data["repositories"][repo_id]["label"] == repo_root


def test_since_last_without_checkpoint_fails_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "checkpoint_path", lambda: tmp_path / "checkpoints.json")
    monkeypatch.setattr(cli, "get_repo_root", lambda repo_path=None: str(tmp_path / "repo"))
    monkeypatch.setattr(
        cli,
        "get_commits",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
    )

    exit_code = cli.main(["--since-last", "--no-ai"])

    assert exit_code == 1
    assert "No since-last checkpoint found" in capsys.readouterr().err


def test_write_checkpoint_updates_after_success_with_no_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    checkpoint_file = tmp_path / "checkpoints.json"
    repo_root = str(tmp_path / "repo")
    repo_id = cli.local_repository_id(repo_root)
    monkeypatch.setattr(cli, "checkpoint_path", lambda: checkpoint_file)
    monkeypatch.setattr(cli, "get_repo_root", lambda repo_path=None: repo_root)
    monkeypatch.setattr(
        cli,
        "_checkpoint_timestamp",
        lambda now=None: "2026-06-08 18:00:00 +0000",
    )
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(["--write-checkpoint", "--no-ai"])

    assert exit_code == 0
    assert captured["until"] == "2026-06-08 18:00:00 +0000"
    data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert data["repositories"][repo_id]["since"] == "2026-06-08 18:00:00 +0000"


def test_write_checkpoint_uses_explicit_until_for_fetch_and_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    checkpoint_file = tmp_path / "checkpoints.json"
    repo_root = str(tmp_path / "repo")
    repo_id = cli.local_repository_id(repo_root)
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "checkpoint_path", lambda: checkpoint_file)
    monkeypatch.setattr(cli, "get_repo_root", lambda repo_path=None: repo_root)
    monkeypatch.setattr(
        cli,
        "_checkpoint_timestamp",
        lambda now=None: "2026-06-08 18:00:00 +0000",
    )

    def fake_get_commits(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(["--until", "2026-06-07", "--write-checkpoint", "--no-ai"])

    assert exit_code == 0
    assert captured["until"] == "2026-06-07"
    data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert data["repositories"][repo_id]["since"] == "2026-06-07"


def test_remote_api_write_checkpoint_uses_generated_until_and_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    checkpoint_file = tmp_path / "checkpoints.json"
    repo_id = cli.remote_repository_id("owner/api")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "checkpoint_path", lambda: checkpoint_file)
    monkeypatch.setattr(
        cli,
        "_checkpoint_timestamp",
        lambda now=None: "2026-06-08 18:30:00 +0000",
    )

    def fake_get_remote_commits(repo: str, **kwargs: object) -> list[dict[str, object]]:
        captured["repo"] = repo
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli, "get_remote_commits", fake_get_remote_commits)

    exit_code = cli.main(
        ["--remote-repo", "owner/api", "--remote-backend", "api", "--write-checkpoint", "--no-ai"]
    )

    assert exit_code == 0
    assert captured["repo"] == "owner/api"
    assert captured["until"] == "2026-06-08 18:30:00 +0000"
    data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert data["repositories"][repo_id]["since"] == "2026-06-08 18:30:00 +0000"
    assert data["repositories"][repo_id]["label"] == "owner/api"


def test_remote_checkpoint_labels_strip_credentials_and_normalize_urls() -> None:
    assert cli._remote_repo_label("https://github.com/owner/api.git") == "owner/api"
    assert cli._remote_repo_label("http://github.com/owner/api.git") == "owner/api"
    assert cli._remote_repo_label("ssh://git@github.com/owner/api.git") == "owner/api"
    assert cli._remote_repo_label("git@github.com:owner/api.git") == "owner/api"

    target = cli._remote_checkpoint_target("ssh://git@github.com/owner/api.git")
    assert target.repository_id == cli.remote_repository_id("owner/api")
    assert target.label == "owner/api"


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://x-access-token:SECRET@github.com/owner/api.git",
        "https://SECRET@github.com/owner/api.git",
        "token@github.com:owner/api.git",
        "x-access-token:SECRET@github.com:owner/api.git",
        "git+ssh://git:SECRET@github.com/owner/api.git",
        "git@github.com:owner/api.git?token=SECRET",
        "git@github.com:owner/api.git#SECRET",
        "https://github.com/owner/api.git?token=SECRET",
    ],
)
def test_remote_checkpoint_labels_reject_credential_bearing_urls(remote_url: str) -> None:
    with pytest.raises(RuntimeError, match="credential-bearing|owner/repo"):
        cli._remote_checkpoint_target(remote_url)


def test_remote_since_last_uses_per_repository_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    checkpoint_file = tmp_path / "checkpoints.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": {
                    cli.remote_repository_id("owner/api"): {"since": "2026-06-08 09:00:00 +0000"},
                    cli.remote_repository_id("owner/web"): {"since": "2026-06-08 10:00:00 +0000"},
                },
            }
        ),
        encoding="utf-8",
    )
    captured: list[tuple[str, object]] = []

    def fake_get_remote_commits(repo: str, **kwargs: object) -> list[dict[str, object]]:
        captured.append((repo, kwargs.get("since")))
        commits = _sample_commits()
        commits[0]["hash"] = repo.split("/")[-1]
        commits[0]["subject"] = f"Report {repo}"
        return commits

    monkeypatch.setattr(cli, "checkpoint_path", lambda: checkpoint_file)
    monkeypatch.setattr(cli, "get_remote_commits", fake_get_remote_commits)

    exit_code = cli.main(
        [
            "--remote-repo",
            "owner/api",
            "--remote-repo",
            "owner/web",
            "--remote-backend",
            "api",
            "--since-last",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured == [
        ("owner/api", "2026-06-08 09:00:00 +0000"),
        ("owner/web", "2026-06-08 10:00:00 +0000"),
    ]
    output = json.loads(capsys.readouterr().out)
    assert set(output["_repositories"]) == {"owner/api", "owner/web"}


def test_markdown_mode_writes_to_output_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    output_path = tmp_path / "standup.md"

    exit_code = cli.main(["--markdown", "--no-ai", "--output", str(output_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert "# Standup Summary" in output_path.read_text(encoding="utf-8")
    assert "- `abc123` Add authentication" in output_path.read_text(encoding="utf-8")


def test_emit_markdown_renders_tty_output_but_copies_raw_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_markdown = "# Standup Summary\n\n- `abc123` Add authentication\n"
    rendered: list[object] = []
    copied: dict[str, object] = {}

    class Tty:
        def isatty(self) -> bool:
            return True

        def write(self, _text: str) -> None:
            return None

        def flush(self) -> None:
            return None

    class FakeConsole:
        def print(self, value: object) -> None:
            rendered.append(value)

    monkeypatch.setattr(cli.sys, "stdout", Tty())
    monkeypatch.setattr(cli, "Console", lambda: FakeConsole())
    monkeypatch.setattr(cli, "Markdown", lambda content: {"markdown": content})
    monkeypatch.setattr(cli, "clipboard_available", lambda: True)
    monkeypatch.setattr(cli, "read_single_key", lambda: "c")
    monkeypatch.setattr(
        cli, "copy_to_clipboard", lambda content: copied.update(content=content) or True
    )

    cli._emit_markdown(raw_markdown, None)

    assert rendered == [{"markdown": raw_markdown}]
    assert copied["content"] == raw_markdown


def test_no_commits_returns_success_with_message(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: [])

    exit_code = cli.main(["--no-ai"])

    assert exit_code == 0
    assert "No commits found" in capsys.readouterr().out


def test_ai_failure_falls_back_to_text_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    def fail_generation(**_: object) -> str:
        raise RuntimeError("missing key")

    monkeypatch.setattr(cli, "generate_standup", fail_generation)

    exit_code = cli.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Warning: AI generation failed" in captured.err
    assert "Weekly Standup Summary" in captured.out


def test_ai_mode_uses_detected_openrouter_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter")
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    def fake_generation(**kwargs: object) -> str:
        captured.update(kwargs)
        return "standup"

    monkeypatch.setattr(cli, "generate_standup", fake_generation)

    exit_code = cli.main([])

    assert exit_code == 0
    assert captured["api_key"] == "sk-openrouter"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["model"] == "openai/gpt-4o-mini"


def test_days_must_be_positive() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--days", "0"])


def test_parse_args_accepts_wizard_command() -> None:
    args = cli.parse_args(["wizard"])

    assert args.command == "wizard"


def test_parse_args_accepts_update_command() -> None:
    args = cli.parse_args(["update"])

    assert args.command == "update"


def test_parse_args_accepts_doctor_command() -> None:
    args = cli.parse_args(["doctor"])

    assert args.command == "doctor"


def test_update_command_runs_pipx_reinstall(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/pipx")
    monkeypatch.setattr(cli, "_host_python", lambda: "/usr/local/bin/python3.11")

    def fake_run(
        cmd: list[str],
        check: bool,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.main(["update"]) == 0
    assert calls == [
        ["/usr/local/bin/pipx", "--version"],
        [
            "/usr/local/bin/pipx",
            "reinstall",
            "git-standup",
            "--python",
            "/usr/local/bin/python3.11",
        ]
    ]


def test_update_command_bootstraps_when_pipx_binary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    bootstrap_dir = Path("/tmp/git-standup-pipx-bootstrap")

    def fake_run(
        cmd: list[str],
        check: bool,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        broken_reinstall = [
            "/broken/pipx",
            "reinstall",
            "git-standup",
            "--python",
            "/usr/local/bin/python3.11",
        ]
        if cmd == broken_reinstall:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli, "_host_python", lambda: "/usr/local/bin/python3.11")
    monkeypatch.setattr(cli, "_pipx_binary", lambda: "/broken/pipx")
    monkeypatch.setattr(cli, "_pipx_bootstrap_dir", lambda: bootstrap_dir)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.main(["update"]) == 0
    assert calls == [
        ["/broken/pipx", "--version"],
        [
            "/broken/pipx",
            "reinstall",
            "git-standup",
            "--python",
            "/usr/local/bin/python3.11",
        ],
        ["/usr/local/bin/python3.11", "-m", "venv", str(bootstrap_dir)],
        [
            str(bootstrap_dir / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "pipx",
        ],
        [
            str(bootstrap_dir / "bin" / "pipx"),
            "reinstall",
            "git-standup",
            "--python",
            "/usr/local/bin/python3.11",
        ],
    ]


def test_update_command_silently_skips_broken_pipx_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    bootstrap_dir = Path("/tmp/git-standup-pipx-bootstrap")

    def fake_run(
        cmd: list[str],
        check: bool,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        if cmd == ["/broken/pipx", "--version"]:
            return subprocess.CompletedProcess(cmd, 1)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli, "_host_python", lambda: "/usr/local/bin/python3.11")
    monkeypatch.setattr(cli, "_pipx_binary", lambda: "/broken/pipx")
    monkeypatch.setattr(cli, "_python_pipx_available", lambda _python: False)
    monkeypatch.setattr(cli, "_pipx_bootstrap_dir", lambda: bootstrap_dir)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.main(["update"]) == 0
    pipx_check = calls[0]
    assert pipx_check[0] == ["/broken/pipx", "--version"]
    assert pipx_check[1]["stdout"] is cli.subprocess.DEVNULL
    assert pipx_check[1]["stderr"] is cli.subprocess.DEVNULL
    assert not any(call[0][0] == "/broken/pipx" and call[0][1] == "reinstall" for call in calls)


def test_update_command_bootstraps_pipx_with_host_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    bootstrap_dir = Path("/tmp/git-standup-pipx-bootstrap")

    def fake_which(name: str) -> str | None:
        return "/usr/bin/python3.11" if name == "python3.11" else None

    def fake_exists(self) -> bool:
        return False

    def fake_run(
        cmd: list[str],
        check: bool,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli, "_python_version_ok", lambda path: True)
    monkeypatch.setattr(cli, "_python_pipx_available", lambda _python: False)
    monkeypatch.setattr(cli, "_pipx_bootstrap_dir", lambda: bootstrap_dir)
    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli.Path, "exists", fake_exists)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.main(["update"]) == 0
    assert calls == [
        ["/usr/bin/python3.11", "-m", "venv", str(bootstrap_dir)],
        [
            str(bootstrap_dir / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "pipx",
        ],
        [
            str(bootstrap_dir / "bin" / "pipx"),
            "reinstall",
            "git-standup",
            "--python",
            "/usr/bin/python3.11",
        ],
    ]


def test_update_command_silently_checks_python_pipx_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    bootstrap_dir = Path("/tmp/git-standup-pipx-bootstrap")

    def fake_which(name: str) -> str | None:
        return "/usr/bin/python3.11" if name == "python3.11" else None

    def fake_exists(self) -> bool:
        return False

    def fake_run(
        cmd: list[str],
        check: bool,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        if cmd == ["/usr/bin/python3.11", "-m", "pipx", "--version"]:
            return subprocess.CompletedProcess(cmd, 1)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli, "_python_version_ok", lambda path: True)
    monkeypatch.setattr(cli, "_pipx_bootstrap_dir", lambda: bootstrap_dir)
    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli.Path, "exists", fake_exists)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.main(["update"]) == 0
    pipx_check = calls[0]
    assert pipx_check[0] == ["/usr/bin/python3.11", "-m", "pipx", "--version"]
    assert pipx_check[1]["stdout"] is cli.subprocess.DEVNULL
    assert pipx_check[1]["stderr"] is cli.subprocess.DEVNULL


def test_update_prompt_uses_line_confirm_not_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "check_for_update",
        lambda: cli.UpdateCheck(True, "old", "new"),
    )
    monkeypatch.setattr(cli, "run_update", lambda: calls.update(updated=True))
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: calls.update(selector=True))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: True)

    assert cli.prompt_for_update_if_available() is True
    assert calls == {"updated": True}


def test_parse_args_accepts_config_subcommands() -> None:
    show = cli.parse_args(["config", "show"])
    assert show.command == "config"
    assert show.config_action == "show"

    set_provider = cli.parse_args(
        [
            "config",
            "set-provider",
            "--provider",
            "gemini",
            "--model",
            "gemini-3.5-flash",
        ]
    )
    assert set_provider.config_action == "set-provider"
    assert set_provider.provider == "gemini"


def test_config_show_reads_saved_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('provider = "gemini"\nmodel = "gemini-3.5-flash"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "config_path", lambda: config_file)

    assert cli.main(["config", "show"]) == 0

    out = capsys.readouterr().out
    assert "provider: gemini" in out
    assert "model: gemini-3.5-flash" in out


def test_doctor_prints_sections_and_masks_detected_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    secret = "sk-doctor-secret-12345"
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "config_path", lambda: config_file)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda _path: cli.AIConfig(provider="openai", model="gpt-4o-mini"),
    )
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"git", "gh"} else None,
    )
    monkeypatch.setattr(cli, "get_repo_root", lambda repo_path=None: str(tmp_path / "repo"))
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    exit_code = cli.main(["doctor"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "git-standup" in captured.out
    assert "Python" in captured.out
    assert "Git" in captured.out
    assert "GitHub CLI" in captured.out
    assert "AI" in captured.out
    assert "Next steps" in captured.out
    assert str(config_file) in captured.out
    assert "repository:" in captured.out
    assert "saved config: provider=openai, model=gpt-4o-mini" in captured.out
    assert secret not in captured.out
    assert secret not in captured.err
    assert "OPENAI_API_KEY=" in captured.out


def test_doctor_returns_error_for_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "config_path", lambda: config_file)

    def boom(_path):
        raise ValueError("bad config")

    monkeypatch.setattr(cli, "load_config", boom)

    exit_code = cli.main(["doctor"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: invalid config: bad config" in captured.err


def test_config_set_provider_saves_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "config_path", lambda: config_file)

    assert cli.main(["config", "set-provider", "--provider", "openrouter"]) == 0

    text = config_file.read_text(encoding="utf-8")
    assert 'provider = "openrouter"' in text
    assert 'model = "openai/gpt-4o-mini"' in text


def test_config_set_cli_accepts_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "config_path", lambda: config_file)

    assert cli.main(["config", "set-cli", "--harness", "codex", "--model", "gpt-5"]) == 0

    text = config_file.read_text(encoding="utf-8")
    assert 'harness = "codex"' in text
    assert 'model = "gpt-5"' in text


def test_config_set_cli_accepts_detected_builtin_harnesses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "config_path", lambda: config_file)
    monkeypatch.setattr(
        cli,
        "detect_ai_environment",
        lambda _env: {
            "api_keys": [],
            "cli_harnesses": ["codex", "opencode", "cursor-agent"],
            "ai_tools": ["codex", "gh", "opencode", "cursor-agent"],
            "unsupported_cli_tools": ["gh"],
        },
    )

    assert cli.main(["config", "set-cli", "--harness", "opencode"]) == 0

    text = config_file.read_text(encoding="utf-8")
    assert 'harness = "opencode"' in text


def test_ai_mode_uses_codex_harness_from_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('harness = "codex"\nmodel = "gpt-5"\n', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_generation(**kwargs: object) -> str:
        captured.update(kwargs)
        return "standup from codex"

    monkeypatch.setattr(cli, "config_path", lambda: config_file)
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    monkeypatch.setattr(cli, "generate_standup_with_harness", fake_generation)

    assert cli.main([]) == 0

    assert captured["harness"] == "codex"
    assert captured["model"] == "gpt-5"


def test_ai_mode_uses_any_builtin_harness_from_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('harness = "opencode"\nmodel = "qwen/code"\n', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_generation(**kwargs: object) -> str:
        captured.update(kwargs)
        return "standup from opencode"

    monkeypatch.setattr(cli, "config_path", lambda: config_file)
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    monkeypatch.setattr(cli, "generate_standup_with_harness", fake_generation)

    assert cli.main([]) == 0

    assert captured["harness"] == "opencode"
    assert captured["model"] == "qwen/code"


def test_ai_provider_available_ignores_only_unsupported_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda _path: None)

    assert not cli._ai_provider_available(
        {
            "api_keys": [],
            "unsupported_api_keys": [{"name": "AZURE_OPENAI_API_KEY"}],
            "cli_harnesses": [],
        }
    )


def test_ai_provider_available_accepts_detected_api_keys_and_harnesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda _path: None)

    assert cli._ai_provider_available(
        {"api_keys": [{"provider": "azure-openai"}], "cli_harnesses": []}
    )
    assert cli._ai_provider_available(
        {"api_keys": [], "cli_harnesses": ["cursor-agent"]}
    )


def test_build_wizard_args_markdown_with_ai_omits_no_ai_flag() -> None:
    args = cli.build_wizard_args(
        {
            "repo": "../api",
            "preset": "branch",
            "base_branch": "develop",
            "format": "markdown",
            "ai": True,
            "output": "standup.md",
        }
    )

    assert args == [
        "--repo",
        "../api",
        "--base-branch",
        "develop",
        "--markdown",
        "--output",
        "standup.md",
    ]


def test_build_wizard_args_markdown_without_ai_adds_no_ai() -> None:
    args = cli.build_wizard_args(
        {"repo": ".", "preset": "week", "format": "markdown", "ai": False}
    )

    assert args == ["--days", "7", "--markdown", "--no-ai"]


def test_build_wizard_args_accepts_multiple_remote_repositories() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["Tresnanda/api", "Tresnanda/web"],
            "preset": "week",
            "format": "markdown",
            "ai": False,
        }
    )

    assert args == [
        "--remote-repo",
        "Tresnanda/api",
        "--remote-repo",
        "Tresnanda/web",
        "--days",
        "7",
        "--markdown",
        "--no-ai",
    ]


def test_build_wizard_args_strips_credentials_from_remote_repository_urls() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": [
                "https://SECRET@github.com/owner/api.git",
                "https://github.com/owner/web.git?token=SECRET",
                "x-access-token:SECRET@github.com:owner/docs.git",
            ],
            "preset": "week",
            "format": "markdown",
            "ai": False,
        }
    )

    assert args == [
        "--remote-repo",
        "owner/api",
        "--remote-repo",
        "owner/web",
        "--remote-repo",
        "owner/docs",
        "--days",
        "7",
        "--markdown",
        "--no-ai",
    ]


def test_build_wizard_args_emits_api_backend() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["owner/api"],
            "remote_backend": "api",
            "preset": "week",
            "format": "markdown",
            "ai": True,
        }
    )
    assert "--remote-backend" in args
    assert args[args.index("--remote-backend") + 1] == "api"


def test_build_wizard_args_clone_backend_omits_backend_flag() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["owner/api"],
            "remote_backend": "clone",
            "preset": "week",
            "format": "markdown",
            "ai": True,
        }
    )
    assert "--remote-backend" not in args


def test_build_wizard_args_emits_all_branches() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["owner/api"],
            "remote_backend": "clone",
            "all_branches": True,
            "preset": "week",
            "format": "markdown",
            "ai": True,
        }
    )
    assert "--all-branches" in args


def test_build_wizard_args_text_with_ai_is_the_default_report() -> None:
    args = cli.build_wizard_args(
        {"repo": ".", "preset": "week", "author": "me", "format": "text", "ai": True}
    )

    assert args == ["--days", "7", "--author", "me"]


def test_build_wizard_args_text_without_ai_adds_no_ai() -> None:
    args = cli.build_wizard_args(
        {"repo": ".", "preset": "week", "author": "me", "format": "text", "ai": False}
    )

    assert args == ["--days", "7", "--author", "me", "--no-ai"]


def test_build_wizard_args_json_ignores_ai_toggle() -> None:
    args = cli.build_wizard_args(
        {"repo": ".", "preset": "today", "since": "2026-06-02", "format": "json", "ai": False}
    )

    assert args == ["--since", "2026-06-02", "--json"]


def test_build_wizard_args_stats_only_is_raw_report() -> None:
    args = cli.build_wizard_args(
        {"repo": ".", "preset": "week", "format": "stats", "ai": True}
    )

    assert args == ["--days", "7", "--stats-only"]


def test_today_start_string_uses_local_midnight_with_timezone() -> None:
    now = datetime(2026, 6, 2, 14, 17, 14, tzinfo=timezone(timedelta(hours=8)))

    assert cli._today_start_string(now) == "2026-06-02 00:00:00 +0800"


def test_parse_args_accepts_explicit_since_timestamp() -> None:
    args = cli.parse_args(["--since", "2026-06-02 00:00:00 +0800"])

    assert args.since == "2026-06-02 00:00:00 +0800"


def test_build_wizard_args_changelog_is_raw_markdown_release_notes() -> None:
    args = cli.build_wizard_args(
        {
            "repo": ".",
            "preset": "custom",
            "days": "14",
            "format": "changelog",
            "ai": True,
            "output": "changelog.md",
        }
    )

    assert args == ["--days", "14", "--changelog", "--output", "changelog.md"]


def test_build_wizard_args_insights_is_raw_planning_report() -> None:
    args = cli.build_wizard_args(
        {"repo": ".", "preset": "week", "format": "insights", "ai": True}
    )

    assert args == ["--days", "7", "--insights"]


def test_build_wizard_args_for_multiple_selected_authors() -> None:
    args = cli.build_wizard_args(
        {
            "repo": ".",
            "preset": "week",
            "authors": ["Alice", "Casey"],
            "format": "text",
            "ai": True,
        }
    )

    assert args == ["--days", "7", "--author", "Alice|Casey"]


def test_default_output_path_matches_output_style() -> None:
    assert cli._default_output_path("text") == "standup.txt"
    assert cli._default_output_path("markdown") == "standup.md"
    assert cli._default_output_path("json") == "standup.json"
    assert cli._default_output_path("changelog") == "changelog.md"
    assert cli._default_output_path("insights") == "standup-insights.md"
    assert cli._default_output_path("stats") == "standup-stats.txt"


def test_numbered_choice_shows_plain_language_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: "2")

    selected = cli._numbered_choice(
        "Report type",
        [
            ("me", "My commits", "Only commits authored by me."),
            ("week", "This week", "Last 7 days for the whole repo."),
        ],
        "week",
    )

    assert selected == "week"
    out = capsys.readouterr().out
    assert "Report type:" in out
    assert "1) My commits - Only commits authored by me." in out
    assert "2) This week - Last 7 days for the whole repo." in out


_AI_AVAILABLE = {"cli_harnesses": [], "api_keys": [{"provider": "openai"}]}


def test_run_wizard_uses_numbered_report_and_output_choices(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["1", "4", "1", "main", "1"])
    captured: dict[str, object] = {}

    def fake_ask(*_args: object, **_kwargs: object) -> str:
        return next(answers)

    # Polish with AI? -> yes, Save? -> no, Run it now -> yes
    confirms = iter([True, False, True])

    def fake_main(args: list[str]) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli.Prompt, "ask", fake_ask)
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    monkeypatch.setattr(cli, "main", fake_main)

    assert cli.run_wizard() == 0

    assert captured["args"] == ["--base-branch", "main", "--markdown"]
    out = capsys.readouterr().out
    assert "Review changes from:" in out
    assert "Branch changes - Compare this branch against a base branch." in out
    assert "By who:" in out
    assert "Everyone - All contributors." in out
    assert "Output format:" in out
    assert "Markdown - Paste-ready for Slack, Notion, or GitHub." in out


def test_run_wizard_starts_with_repository_source_and_remote_multi_select(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # repo source -> remote(3), repos -> 1,2, backend -> clone(1), preset -> week(2),
    # author -> all(1), format -> markdown(1)
    answers = iter(["3", "1,2", "1", "2", "1", "1"])
    # All branches? -> yes, Polish with AI? -> yes, Save? -> no, Run it now -> no
    confirms = iter([True, True, False, False])

    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    monkeypatch.setattr(
        cli,
        "_remote_repository_groups",
        lambda: {
            "Owned": ["Tresnanda/api", "Tresnanda/web"],
            "Organizations": ["Tresnanda/docs"],
            "Collaborator": [],
        },
    )

    assert cli.run_wizard() == 0

    out = capsys.readouterr().out
    assert "Repository source:" in out
    assert "Current directory - Use this Git repository." in out
    assert "Remote repository - Pick one or more GitHub repositories." in out
    assert "Choose remote repositories:" in out
    assert "Remote backend:" in out
    assert "1) Tresnanda/api" in out
    assert "2) Tresnanda/web" in out
    assert (
        "Generated command:\n  git-standup --remote-repo Tresnanda/api "
        "--remote-repo Tresnanda/web --all-branches --days 7 --markdown"
    ) in out


def test_run_wizard_asks_timeframe_then_author_then_format(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompts: list[tuple[str, dict[str, object]]] = []
    answers = iter(["1", "2", "2", "2"])
    # Polish with AI? -> yes, Save? -> no, Run it now -> no
    confirms = iter([True, False, False])

    def fake_ask(message: str, **kwargs: object) -> str:
        prompts.append((message, kwargs))
        return next(answers)

    monkeypatch.setattr(cli.Prompt, "ask", fake_ask)
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))

    assert cli.run_wizard() == 0

    assert prompts[1] == (
        "Review changes from choice",
        {"choices": ["1", "2", "3", "4"], "default": "2"},
    )
    assert prompts[2] == ("By who choice", {"choices": ["1", "2", "3"], "default": "1"})
    assert prompts[3] == (
        "Output format choice",
        {"choices": ["1", "2", "3", "4", "5", "6"], "default": "1"},
    )
    out = capsys.readouterr().out
    assert "Review changes from:" in out
    assert "This week - Last 7 days." in out
    assert "By who:" in out
    assert "Me - Only commits authored by me." in out
    assert "Generated command:\n  git-standup --days 7 --author me" in out


def test_run_wizard_guides_file_saving(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompts: list[tuple[str, dict[str, object]]] = []
    answers = iter(["1", "2", "2", "2", "standup.txt"])
    # Polish with AI? -> yes, Save? -> yes, Run it now -> no
    confirms = iter([True, True, False])

    def fake_ask(message: str, **kwargs: object) -> str:
        prompts.append((message, kwargs))
        return next(answers)

    monkeypatch.setattr(cli.Prompt, "ask", fake_ask)
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))

    assert cli.run_wizard() == 0

    assert prompts[4] == ("Save as", {"default": "standup.txt"})
    out = capsys.readouterr().out
    assert "This week - Last 7 days." in out
    assert "Me - Only commits authored by me." in out
    assert "Plain text - Simple terminal summary." in out
    assert "Generated command:\n  git-standup --days 7 --author me --output standup.txt" in out


def test_run_wizard_uses_multi_author_picker_for_someone_else(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["1", "2", "3", "1,3", "2"])
    # Polish with AI? -> yes, Save? -> no, Run it now -> yes
    confirms = iter([True, False, True])
    captured: dict[str, object] = {}

    def fake_main(args: list[str]) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    monkeypatch.setattr(cli, "_recent_authors", lambda _repo: ["Alice", "Bob", "Casey"])
    monkeypatch.setattr(cli, "main", fake_main)

    assert cli.run_wizard() == 0

    assert captured["args"] == ["--days", "7", "--author", "Alice|Casey"]
    out = capsys.readouterr().out
    assert "Choose authors:" in out
    assert "1) Alice" in out
    assert "3) Casey" in out
    assert "Generated command:\n  git-standup --days 7 --author 'Alice|Casey'" in out


def test_multi_select_uses_arrow_keys_and_space_to_select(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = iter([" ", "\x1b[B", " ", "\r"])

    selected = cli._interactive_multi_select(
        "Choose authors",
        ["Kevin", "YusufRehan", "Treshnanda"],
        key_reader=lambda: next(keys),
    )

    assert selected == ["Kevin", "YusufRehan"]
    out = capsys.readouterr().out
    assert "Choose authors" in out
    assert "space select" in out


def test_multi_select_pages_long_option_lists(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_terminal_lines", lambda: 10)
    options = [f"Tresnanda/repo-{index}" for index in range(1, 21)]

    selected = cli._interactive_multi_select(
        "Choose remote repositories",
        options,
        key_reader=lambda: "\r",
    )

    assert selected == []
    out = capsys.readouterr().out
    assert "Tresnanda/repo-1" in out
    assert "Tresnanda/repo-6" in out
    assert "Tresnanda/repo-7" not in out
    assert "Showing 1-6 of 20. Selected: 0." in out


def test_multi_select_scrolls_viewport_with_cursor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_terminal_lines", lambda: 10)
    keys = iter(["\x1b[B", "\x1b[B", "\x1b[B", "\x1b[B", "\x1b[B", "\x1b[B", "\r"])
    options = [f"Tresnanda/repo-{index}" for index in range(1, 21)]

    cli._interactive_multi_select(
        "Choose remote repositories",
        options,
        key_reader=lambda: next(keys),
    )

    out = capsys.readouterr().out
    assert "\x1b[36m❯\x1b[0m [ ] Tresnanda/repo-7" in out
    assert "Showing 4-9 of 20. Selected: 0." in out


def test_tabbed_multi_select_uses_horizontal_arrows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = iter(["\x1b[C", " ", "\r"])

    selected = cli._interactive_tabbed_multi_select(
        "Choose remote repositories",
        {
            "Owned": ["me/api"],
            "Organizations": ["org/web"],
            "Collaborator": ["friend/tool"],
        },
        key_reader=lambda: next(keys),
    )

    assert selected == ["org/web"]
    out = capsys.readouterr().out
    assert "Tabs: Owned | [Organizations] | Collaborator" in out
    assert "←/→ tabs · ↑/↓ move · space select · ⏎ confirm · a all · q quit" in out
    assert "\x1b[36m❯\x1b[0m [ ] org/web" in out


def test_interactive_choice_uses_arrow_keys_to_select(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = iter(["\x1b[B", "\r"])

    selected = cli._interactive_choice(
        "Output format",
        [
            ("markdown", "Markdown", "Paste-ready Markdown."),
            ("text", "Plain text", "Simple terminal summary."),
        ],
        "markdown",
        key_reader=lambda: next(keys),
    )

    assert selected == "text"
    out = capsys.readouterr().out
    assert "\x1b[1mOutput format\x1b[0m" in out
    assert "↑/↓ move · ⏎ select · q quit" in out
    assert "\x1b[4F\x1b[J" in out


def test_interactive_choice_q_cancels_instead_of_selecting_default() -> None:
    with pytest.raises(cli._WizardCancelled):
        cli._interactive_choice(
            "Repository source",
            [
                ("current", "Current directory", "Use this Git repository."),
                ("remote", "Remote repository", "Pick one or more GitHub repositories."),
            ],
            "current",
            key_reader=lambda: "q",
        )


def test_interactive_multi_select_q_cancels() -> None:
    with pytest.raises(cli._WizardCancelled):
        cli._interactive_multi_select(
            "Choose remote repositories",
            ["Tresnanda/git-standup"],
            key_reader=lambda: "q",
        )


def test_interactive_confirm_uses_same_selector() -> None:
    keys = iter(["\x1b[B", "\r"])

    assert cli._interactive_confirm(
        "Run it now",
        default=True,
        key_reader=lambda: next(keys),
    ) is False


def test_run_wizard_q_cancels_before_generated_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: dict[str, object] = {}

    def cancel_choice(*_args: object, **_kwargs: object) -> str:
        raise cli._WizardCancelled

    monkeypatch.setattr(cli, "_numbered_choice", cancel_choice)
    monkeypatch.setattr(cli, "main", lambda _args: called.update(main=True) or 0)

    assert cli.run_wizard() == 0
    assert called == {}
    out = capsys.readouterr().out
    assert "Cancelled." in out
    assert "Generated command" not in out


def test_terminal_key_reader_reads_full_arrow_escape_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TtyStdin:
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 99

    reads = iter([b"\x1b", b"[", b"B"])
    ready = iter([([99], [], []), ([99], [], []), ([], [], [])])
    fake_termios = types.SimpleNamespace(
        TCSADRAIN=1,
        tcgetattr=lambda _fd: "old",
        tcsetattr=lambda *_args: None,
    )
    fake_tty = types.SimpleNamespace(setcbreak=lambda _fd: None)

    monkeypatch.setattr(cli.sys, "stdin", TtyStdin())
    monkeypatch.setitem(cli.sys.modules, "msvcrt", None)
    monkeypatch.setitem(cli.sys.modules, "termios", fake_termios)
    monkeypatch.setitem(cli.sys.modules, "tty", fake_tty)
    monkeypatch.setattr(cli.os, "read", lambda _fd, _size: next(reads))
    monkeypatch.setattr(cli.select, "select", lambda *_args: next(ready))

    assert cli._read_terminal_key() == "\x1b[B"


def test_raw_terminal_session_preserves_output_newline_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("termios")
    import pty
    import termios

    primary, secondary = pty.openpty()

    class PtyStdin:
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return secondary

    monkeypatch.setattr(cli.sys, "stdin", PtyStdin())
    monkeypatch.setattr(cli.sys, "platform", "linux")

    try:
        with cli._raw_terminal_session(True):
            attrs = termios.tcgetattr(secondary)
        # OPOST (output post-processing, which maps \n -> \r\n) must stay on so
        # the picker's print()-based redraw doesn't staircase across the screen.
        assert attrs[1] & termios.OPOST
    finally:
        os.close(primary)
        os.close(secondary)


def test_clone_remote_repo_does_not_use_blobless_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A --filter=blob:none clone makes `git log --numstat` fetch every blob over
    # the network on demand, which times out. The clone must fetch blobs upfront.
    for which_result in ("/usr/bin/gh", None):
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(cli.shutil, "which", lambda _name: which_result)
        monkeypatch.setattr(cli.subprocess, "run", fake_run)

        cli._clone_remote_repo("owner/name", tmp_path)

        assert "--filter=blob:none" not in captured["cmd"]


def test_clone_remote_repo_surfaces_git_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=128, cmd=cmd, output="", stderr="ERROR: SAML SSO enforced"
        )

    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        cli._clone_remote_repo("owner/name", tmp_path)

    message = str(excinfo.value)
    assert "owner/name" in message
    assert "SAML SSO enforced" in message


def test_clone_remote_repo_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        cli._clone_remote_repo("owner/name", tmp_path)

    assert "timed out" in str(excinfo.value)


def test_parse_args_all_branches_defaults_false() -> None:
    args = cli.parse_args(["--remote-repo", "owner/name"])
    assert args.all_branches is False


def test_parse_args_accepts_all_branches() -> None:
    args = cli.parse_args(["--remote-repo", "owner/name", "--all-branches"])
    assert args.all_branches is True


def test_api_backend_supports_all_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_remote_commits(repo, **kwargs):
        captured.update(kwargs)
        return _sample_commits()

    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    monkeypatch.setattr(cli, "get_remote_commits", fake_get_remote_commits)
    exit_code = cli.main(
        [
            "--remote-repo",
            "owner/name",
            "--remote-backend",
            "api",
            "--all-branches",
            "--no-ai",
            "--markdown",
        ]
    )
    assert exit_code == 0
    assert captured["all_branches"] is True


def test_clone_backend_passes_all_branches_to_get_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs):
        captured.update(kwargs)
        return _sample_commits()

    monkeypatch.setattr(cli, "_clone_remote_repo", lambda repo, parent: tmp_path)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", lambda: _TempDir(tmp_path))
    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(
        ["--remote-repo", "owner/name", "--all-branches", "--no-ai", "--markdown"]
    )

    assert exit_code == 0
    assert captured["all_branches"] is True


def test_interactive_choice_collapses_to_summary_on_confirm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = iter(["\x1b[B", "\r"])

    cli._interactive_choice(
        "Output format",
        [
            ("markdown", "Markdown", "Paste-ready Markdown."),
            ("text", "Plain text", "Simple terminal summary."),
        ],
        "markdown",
        key_reader=lambda: next(keys),
    )

    out = capsys.readouterr().out
    assert "\x1b[32m✓\x1b[0m Output format · Plain text" in out


def test_multi_select_collapses_to_summary_on_confirm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = iter([" ", "\x1b[B", " ", "\r"])

    cli._interactive_multi_select(
        "Choose authors",
        ["Kevin", "YusufRehan", "Treshnanda"],
        key_reader=lambda: next(keys),
    )

    out = capsys.readouterr().out
    assert "\x1b[32m✓\x1b[0m Choose authors · Kevin, YusufRehan" in out


def test_multi_select_empty_selection_summary_says_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._interactive_multi_select(
        "Choose authors",
        ["Kevin"],
        key_reader=lambda: "\r",
    )

    assert "\x1b[32m✓\x1b[0m Choose authors · none" in capsys.readouterr().out


def test_multi_select_add_row_prompts_and_appends_custom_entry() -> None:
    # Up to the add row (cursor starts on the first real repo), Enter to add, Enter to confirm.
    keys = iter(["\x1b[A", "\r", "\r"])

    selected = cli._interactive_multi_select(
        "Choose remote repositories",
        ["me/api", "me/web"],
        key_reader=lambda: next(keys),
        add_label=cli._ADD_CUSTOM_REPO_LABEL,
        add_prompt=lambda: ["owner/custom"],
    )

    assert selected == ["owner/custom"]


def test_multi_select_add_row_shown_without_checkbox(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._interactive_multi_select(
        "Choose remote repositories",
        ["me/api"],
        key_reader=lambda: "\r",
        add_label=cli._ADD_CUSTOM_REPO_LABEL,
        add_prompt=lambda: [],
    )

    out = capsys.readouterr().out
    assert cli._ADD_CUSTOM_REPO_LABEL in out
    assert f"[ ] {cli._ADD_CUSTOM_REPO_LABEL}" not in out


def test_remote_repositories_lists_owner_and_collaborator_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        joined = " ".join(cmd)
        if "affiliation=owner" in joined:
            stdout = "me/web\nme/api\n"
        elif "affiliation=organization_member" in joined:
            stdout = "org/api\n"
        else:
            stdout = "me/web\n"
        return types.SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    repos = cli._remote_repositories()

    assert repos == ["me/api", "me/web", "org/api"]
    assert len(captured) == 3


def test_remote_repository_groups_are_split_by_affiliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        joined = " ".join(cmd)
        if "affiliation=owner" in joined:
            stdout = "me/api\nme/docs\n"
        elif "affiliation=organization_member" in joined:
            stdout = "org/web\n"
        else:
            stdout = "friend/tool\n"
        return types.SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    groups = cli._remote_repository_groups()

    assert groups == {
        "Owned": ["me/api", "me/docs"],
        "Organizations": ["org/web"],
        "Collaborator": ["friend/tool"],
    }
    assert len(captured) == 3
    assert "affiliation=owner" in " ".join(captured[0])
    assert "affiliation=organization_member" in " ".join(captured[1])
    assert "affiliation=collaborator" in " ".join(captured[2])


def test_remote_repositories_returns_empty_without_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    assert cli._remote_repositories() == []


def test_wizard_separator_prints_full_width_rule(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli.shutil,
        "get_terminal_size",
        lambda fallback=(80, 24): os.terminal_size((40, 24)),
    )

    cli._wizard_separator()

    assert "─" * 40 in capsys.readouterr().out


def test_spinner_prints_message_once_when_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)

    ran = False
    with cli._spinner("Working…"):
        ran = True

    assert ran
    assert "Working…" in capsys.readouterr().out


def test_main_opens_wizard_for_bare_interactive_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(cli.sys, "stdin", Tty())
    monkeypatch.setattr(cli.sys, "stdout", Tty())
    monkeypatch.setattr(cli, "run_wizard", lambda: 0)

    assert cli.main([]) == 0


def test_markdown_with_ai_uses_ai_path_with_markdown_format(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    captured: dict[str, object] = {}

    def fake_generation(**kwargs: object) -> str:
        captured.update(kwargs)
        return "# AI markdown report"

    monkeypatch.setattr(cli, "generate_standup", fake_generation)

    assert cli.main(["--markdown"]) == 0

    assert captured["output_format"] == "markdown"
    assert "# AI markdown report" in capsys.readouterr().out


def test_json_with_ai_flag_warns_and_stays_raw(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    assert cli.main(["--json", "--ai"]) == 0

    captured = capsys.readouterr()
    assert "--ai has no effect with --json" in captured.err
    assert json.loads(captured.out)["Alice"]["2026-03-10"]["stats"]["total_commits"] == 1


def test_configure_ai_interactive_sets_key_and_saves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    secret = "pasted-secret-token-12345"
    config_file = tmp_path / "config.toml"
    choices = iter(["provider", "openai"])
    getpass_prompts: list[str] = []
    monkeypatch.setattr(cli, "_choice", lambda *_a, **_k: next(choices))
    monkeypatch.setattr(cli, "_prompt_model", lambda default: default)
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda prompt="": getpass_prompts.append(prompt) or secret,
    )
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_a, **_k: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config = cli.configure_ai_interactive(config_file)
    captured = capsys.readouterr()

    assert config is not None
    assert config.provider == "openai"
    assert os.environ["OPENAI_API_KEY"] == secret
    assert getpass_prompts == ["OPENAI_API_KEY (hidden; leave blank to skip): "]
    assert secret not in captured.out
    assert secret not in captured.err
    assert "export OPENAI_API_KEY" not in captured.out
    assert "secret value is not printed here" in captured.out
    text = config_file.read_text(encoding="utf-8")
    assert 'provider = "openai"' in text
    assert "api_key" not in text  # secrets never written to config
    assert secret not in text


def test_configure_ai_interactive_custom_provider_uses_configured_key_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    secret = "gateway-secret-token-67890"
    config_file = tmp_path / "config.toml"
    choices = iter(["provider", "custom"])
    prompts: list[tuple[str, str]] = []
    getpass_prompts: list[str] = []
    monkeypatch.setattr(cli, "_choice", lambda *_a, **_k: next(choices))
    monkeypatch.setattr(cli, "_prompt_model", lambda default: default)
    monkeypatch.setattr(
        cli.Prompt,
        "ask",
        lambda prompt, default="": prompts.append((prompt, str(default)))
        or (
            "https://gateway.example/v1"
            if prompt == "OpenAI-compatible base URL"
            else "MY_GATEWAY_KEY"
        ),
    )
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda prompt="": getpass_prompts.append(prompt) or secret,
    )
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_a, **_k: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)

    config = cli.configure_ai_interactive(config_file)
    captured = capsys.readouterr()

    assert config is not None
    assert config.provider == "custom"
    assert config.base_url == "https://gateway.example/v1"
    assert config.api_key_env == "MY_GATEWAY_KEY"
    assert os.environ["MY_GATEWAY_KEY"] == secret
    assert "OPENAI_API_KEY" not in os.environ
    assert prompts == [
        ("OpenAI-compatible base URL", "https://api.openai.com/v1"),
        ("API key environment variable", "OPENAI_API_KEY"),
    ]
    assert getpass_prompts == ["MY_GATEWAY_KEY (hidden; leave blank to skip): "]
    assert secret not in captured.out
    assert secret not in captured.err
    text = config_file.read_text(encoding="utf-8")
    assert 'provider = "custom"' in text
    assert 'base_url = "https://gateway.example/v1"' in text
    assert 'api_key_env = "MY_GATEWAY_KEY"' in text
    assert secret not in text


def test_configure_ai_interactive_prompts_for_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    choices = iter(["provider", "openai"])
    seen_defaults: list[str] = []

    def fake_prompt_model(default: str) -> str:
        seen_defaults.append(default)
        return "gpt-4o"

    monkeypatch.setattr(cli, "_choice", lambda *_a, **_k: next(choices))
    monkeypatch.setattr(cli, "_prompt_model", fake_prompt_model)
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "sk-entered")
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_a, **_k: False)

    config = cli.configure_ai_interactive(config_file)

    assert seen_defaults == ["gpt-4o-mini"]  # provider default offered as the prefilled value
    assert config is not None
    assert config.model == "gpt-4o"


def test_configure_ai_interactive_cli_prompts_for_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_file = tmp_path / "config.toml"
    choices = iter(["cli", "ollama"])
    monkeypatch.setattr(cli, "_choice", lambda *_a, **_k: next(choices))
    monkeypatch.setattr(cli, "_prompt_model", lambda _default: "mistral-nemo")

    config = cli.configure_ai_interactive(config_file)

    assert config is not None
    assert config.harness == "ollama"
    assert config.model == "mistral-nemo"


def test_configure_ai_interactive_explicit_provider_does_not_prompt_for_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    # The scripted `config set-provider --provider X` path must never prompt
    # (which would hang on a non-interactive stdin); it keeps the default model.
    config_file = tmp_path / "config.toml"

    def boom(_default: str) -> str:
        raise AssertionError("_prompt_model should not be called when provider is explicit")

    monkeypatch.setattr(cli, "_prompt_model", boom)

    config = cli.configure_ai_interactive(
        config_file, kind="provider", provider="openrouter", allow_key=False
    )

    assert config is not None
    assert config.model == "openai/gpt-4o-mini"


def test_maybe_offer_copy_copies_on_c_keypress(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: dict[str, object] = {}

    class Tty:
        def isatty(self) -> bool:
            return True

        def write(self, _text: str) -> None:
            return None

        def flush(self) -> None:
            return None

    monkeypatch.setattr(cli.sys, "stdout", Tty())
    monkeypatch.setattr(cli, "clipboard_available", lambda: True)
    monkeypatch.setattr(cli, "read_single_key", lambda: "c")
    monkeypatch.setattr(
        cli, "copy_to_clipboard", lambda content: copied.update(content=content) or True
    )

    cli._maybe_offer_copy("REPORT")

    assert copied["content"] == "REPORT"


def test_maybe_offer_copy_skips_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    class NotTty:
        def isatty(self) -> bool:
            return False

        def write(self, _text: str) -> None:
            return None

        def flush(self) -> None:
            return None

    monkeypatch.setattr(cli.sys, "stdout", NotTty())
    monkeypatch.setattr(cli, "copy_to_clipboard", lambda content: called.update(hit=True) or True)

    cli._maybe_offer_copy("REPORT")

    assert "hit" not in called
