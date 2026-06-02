import pytest

from git_standup import env_persist


def test_persist_appends_export_to_zshrc(tmp_path) -> None:
    target = env_persist.persist_env_var(
        "OPENAI_API_KEY",
        "sk-123",
        env={"SHELL": "/bin/zsh"},
        home=tmp_path,
    )

    assert target == str(tmp_path / ".zshrc")
    assert "export OPENAI_API_KEY='sk-123'" in (tmp_path / ".zshrc").read_text(encoding="utf-8")


def test_persist_dedupes_existing_var(tmp_path) -> None:
    profile = tmp_path / ".zshrc"
    profile.write_text("export OPENAI_API_KEY='old'\nexport PATH=$PATH\n", encoding="utf-8")

    env_persist.persist_env_var(
        "OPENAI_API_KEY",
        "new",
        env={"SHELL": "/bin/zsh"},
        home=tmp_path,
    )

    text = profile.read_text(encoding="utf-8")
    assert text.count("OPENAI_API_KEY") == 1
    assert "export OPENAI_API_KEY='new'" in text
    assert "export PATH=$PATH" in text


def test_persist_uses_bashrc_for_bash(tmp_path) -> None:
    target = env_persist.persist_env_var(
        "GROQ_API_KEY",
        "key",
        env={"SHELL": "/usr/bin/bash"},
        home=tmp_path,
    )

    assert target == str(tmp_path / ".bashrc")


def test_persist_escapes_single_quotes(tmp_path) -> None:
    env_persist.persist_env_var(
        "OPENAI_API_KEY",
        "a'b",
        env={"SHELL": "/bin/zsh"},
        home=tmp_path,
    )

    text = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert "export OPENAI_API_KEY='a'\"'\"'b'" in text


def test_persist_uses_setx_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(env_persist.os, "name", "nt")

    def fake_run(command, check, capture_output):
        calls["command"] = command
        return None

    monkeypatch.setattr(env_persist.subprocess, "run", fake_run)

    target = env_persist.persist_env_var("OPENAI_API_KEY", "sk-9", home=tmp_path)

    assert target == "setx"
    assert calls["command"] == ["setx", "OPENAI_API_KEY", "sk-9"]
