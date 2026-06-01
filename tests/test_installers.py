from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_unix_installer_uses_numbered_ai_setup_and_key_entry() -> None:
    text = _read("install.sh")

    assert "Choose AI default:" in text
    assert "1) Codex CLI" in text
    assert "2) OpenAI API" in text
    assert "3) Gemini API" in text
    assert "4) OpenRouter API" in text
    assert "5) Skip AI setup" in text
    assert "Paste API key now" in text
    assert "save_secret_to_shell_profile" in text
    assert 'harness = "codex"' in text
    assert "Run $APP_NAME wizard now?" not in text
    assert '"$APP_NAME" wizard' not in text
    assert "Run git-standup in your terminal to start the guided report builder." in text


def test_windows_installer_uses_numbered_ai_setup_and_key_entry() -> None:
    text = _read("install.ps1")

    assert "Choose AI default:" in text
    assert "1) Codex CLI" in text
    assert "2) OpenAI API" in text
    assert "3) Gemini API" in text
    assert "4) OpenRouter API" in text
    assert "5) Skip AI setup" in text
    assert "Paste API key now" in text
    assert "Save-UserSecret" in text
    assert "harness = `\"codex`\"" in text
    assert "Run $AppName wizard now?" not in text
    assert "& $AppName wizard" not in text
    assert "Run git-standup in your terminal to start the guided report builder." in text
