import json

import pytest

from git_standup import cli


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


def test_json_mode_prints_structured_commit_data(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    exit_code = cli.main(["--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["Alice"]["2026-03-10"]["stats"]["total_commits"] == 1
    assert output["Alice"]["2026-03-10"]["commits"][0]["subject"] == "Add authentication"


def test_markdown_mode_prints_paste_ready_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    exit_code = cli.main(["--markdown"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "# Standup Summary" in output
    assert "## Alice" in output
    assert "- `abc123` Add authentication" in output


def test_main_passes_repo_and_exact_dates_to_gitlog(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["repo_path"] == "/workspace/app"
    assert captured["since"] == "2026-01-01"
    assert captured["until"] == "2026-01-07"


def test_parse_args_supports_easy_presets_and_positional_repo() -> None:
    me = cli.parse_args(["me"])
    assert me.author == "me"
    assert me.no_ai is True

    branch = cli.parse_args(["branch"])
    assert branch.base_branch == "main"
    assert branch.no_ai is True

    repo = cli.parse_args(["../api", "--markdown", "--out", "standup.md"])
    assert repo.repo == "../api"
    assert repo.markdown is True
    assert repo.output == "standup.md"


def test_markdown_mode_writes_to_output_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())
    output_path = tmp_path / "standup.md"

    exit_code = cli.main(["--markdown", "--output", str(output_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert "# Standup Summary" in output_path.read_text(encoding="utf-8")
    assert "- `abc123` Add authentication" in output_path.read_text(encoding="utf-8")


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


def test_build_wizard_args_for_branch_markdown_report() -> None:
    args = cli.build_wizard_args(
        {
            "repo": "../api",
            "preset": "branch",
            "base_branch": "develop",
            "format": "markdown",
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
