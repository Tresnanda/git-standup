import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_standup import cli
from git_standup.formatter import build_markdown_output


@pytest.fixture(autouse=True)
def isolated_user_ai_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "config_path", lambda: tmp_path / "config.toml")
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


def test_json_mode_prints_structured_commit_data(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_commits", lambda **_: _sample_commits())

    exit_code = cli.main(["--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["Alice"]["2026-03-10"]["stats"]["total_commits"] == 1
    assert output["Alice"]["2026-03-10"]["commits"][0]["subject"] == "Add authentication"
    assert "_metadata" not in output


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
    assert output["_metadata"] == {"pathspecs": ["src", "tests"]}


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
    assert set(output["_repositories"]) == {"Tresnanda/api", "Tresnanda/web"}
    assert output["_repositories"]["Tresnanda/api"]["Alice"]["2026-03-10"]["commits"][0][
        "subject"
    ] == "Report Tresnanda__api"


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
    assert captured["pathspecs"] == ["src"]
    assert captured["exclude_merges"] is True


def test_parse_args_supports_easy_presets_and_positional_repo() -> None:
    default = cli.parse_args([])
    assert default.exclude_merges is False

    me = cli.parse_args(["me"])
    assert me.author == "me"
    assert me.no_ai is True

    no_merges = cli.parse_args(["--exclude-merges"])
    assert no_merges.exclude_merges is True

    branch = cli.parse_args(["branch"])
    assert branch.base_branch == "main"
    assert branch.no_ai is True

    repo = cli.parse_args(["../api", "--markdown", "--out", "standup.md"])
    assert repo.repo == "../api"
    assert repo.markdown is True
    assert repo.output == "standup.md"

    changelog = cli.parse_args(["--changelog"])
    assert changelog.changelog is True

    paths = cli.parse_args(["--path", "src", "--pathspec", "README.md"])
    assert paths.pathspecs == ["src", "README.md"]


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
    answers = iter(["3", "1,2", "2", "1", "1"])
    # Polish with AI? -> yes, Save? -> no, Run it now -> no
    confirms = iter([True, False, False])

    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    monkeypatch.setattr(
        cli,
        "_remote_repositories",
        lambda: ["Tresnanda/api", "Tresnanda/web", "Tresnanda/docs"],
    )

    assert cli.run_wizard() == 0

    out = capsys.readouterr().out
    assert "Repository source:" in out
    assert "Current directory - Use this Git repository." in out
    assert "Remote repository - Pick one or more GitHub repositories." in out
    assert "Choose remote repositories:" in out
    assert "1) Tresnanda/api" in out
    assert "2) Tresnanda/web" in out
    assert (
        "Generated command:\n  git-standup --remote-repo Tresnanda/api "
        "--remote-repo Tresnanda/web --days 7 --markdown"
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
        {"choices": ["1", "2", "3", "4"], "default": "1"},
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
    assert "Space selects" in out


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
    assert "Output format:" in out
    assert "Use Up/Down to move" in out
    assert "\x1b[4F\x1b[J" in out


def test_interactive_confirm_uses_same_selector() -> None:
    keys = iter(["\x1b[B", "\r"])

    assert cli._interactive_confirm(
        "Run it now",
        default=True,
        key_reader=lambda: next(keys),
    ) is False


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
    fake_tty = types.SimpleNamespace(setraw=lambda _fd: None)

    monkeypatch.setattr(cli.sys, "stdin", TtyStdin())
    monkeypatch.setitem(cli.sys.modules, "msvcrt", None)
    monkeypatch.setitem(cli.sys.modules, "termios", fake_termios)
    monkeypatch.setitem(cli.sys.modules, "tty", fake_tty)
    monkeypatch.setattr(cli.os, "read", lambda _fd, _size: next(reads))
    monkeypatch.setattr(cli.select, "select", lambda *_args: next(ready))

    assert cli._read_terminal_key() == "\x1b[B"


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
) -> None:
    config_file = tmp_path / "config.toml"
    choices = iter(["provider", "openai"])
    monkeypatch.setattr(cli, "_choice", lambda *_a, **_k: next(choices))
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "sk-entered")
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_a, **_k: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config = cli.configure_ai_interactive(config_file)

    assert config is not None
    assert config.provider == "openai"
    assert os.environ["OPENAI_API_KEY"] == "sk-entered"
    text = config_file.read_text(encoding="utf-8")
    assert 'provider = "openai"' in text
    assert "api_key" not in text  # secrets never written to config


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
