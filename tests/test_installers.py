from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_unix_installer_uses_numbered_ai_setup_and_key_entry() -> None:
    text = _read("install.sh")

    assert "python_version_ok()" in text
    assert "python3.13 python3.12 python3.11 python3.10 python3 python" in text
    assert "Python 3.10+ is required." in text
    assert 'install --python "$PYTHON" --force "$REPO_SPEC"' in text
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
    assert "If $APP_NAME helps you, star the GitHub repo now?" in text
    assert "gh repo star \"$REPO_SLUG\"" in text
    assert "api.github.com/user/starred/$REPO_SLUG" in text
    assert "Star it here: $REPO_URL" in text
    assert "Run git-standup in your terminal to start the guided report builder." in text


def test_windows_installer_uses_numbered_ai_setup_and_key_entry() -> None:
    text = _read("install.ps1")

    assert "$MinimumPythonMajor = 3" in text
    assert "$MinimumPythonMinor = 10" in text
    assert "Test-PythonVersion" in text
    assert "Resolve-PythonExecutable" in text
    assert '"--python", $Python, "--force", $RepoSpec' in text
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
    assert "If $AppName helps you, star the GitHub repo now?" in text
    assert "gh repo star $RepoSlug" in text
    assert "api.github.com/user/starred/$RepoSlug" in text
    assert "Star it here: $RepoUrl" in text
    assert "Run git-standup in your terminal to start the guided report builder." in text


def test_ci_checks_installer_script_syntax() -> None:
    text = _read(".github/workflows/ci.yml")

    assert "bash -n install.sh" in text
    assert 'ParseFile("install.ps1"' in text


def test_ci_opts_into_current_node_runtime_for_actions() -> None:
    text = _read(".github/workflows/ci.yml")

    assert "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true" in text
