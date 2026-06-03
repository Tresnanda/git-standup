from pathlib import Path

from git_standup.ai_env import CLI_HARNESS_SPECS, PROVIDER_SPECS

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _provider_label(provider: str) -> str:
    return {
        "openai": "OpenAI API",
        "gemini": "Gemini API",
        "openrouter": "OpenRouter API",
        "groq": "Groq API",
        "mistral": "Mistral API",
        "together": "Together AI API",
        "perplexity": "Perplexity API",
        "xai": "xAI API",
    }[provider]


def test_unix_installer_uses_numbered_ai_setup_and_key_entry() -> None:
    text = _read("install.sh")

    assert "python_version_ok()" in text
    assert "python3.13 python3.12 python3.11 python3.10 python3 python" in text
    assert "Python 3.10+ is required." in text
    assert "bootstrap_pipx()" in text
    assert 'pipx-bootstrap' in text
    assert ' -m venv "$venv_dir"' in text
    assert 'pip install --upgrade pip pipx' in text
    assert 'pipx_path="$(command -v pipx)"' in text
    assert '"$pipx_path" --version' in text
    assert "Ignoring broken pipx" in text
    assert "install_or_update_app()" in text
    assert 'reinstall "$APP_NAME" --python "$PYTHON"' in text
    assert 'install --python "$PYTHON" "$REPO_SPEC"' in text
    assert "Choose AI default:" in text
    for index, harness in enumerate(CLI_HARNESS_SPECS, start=1):
        assert f'{harness.command}) echo "{harness.label}" ;;' in text
        assert f'{index}) $(harness_label {harness.command})' in text
        assert f'{index}) write_harness_config "{harness.command}"' in text
    provider_start = len(CLI_HARNESS_SPECS) + 1
    for index, spec in enumerate(PROVIDER_SPECS, start=provider_start):
        assert f'{spec.provider}) echo "{_provider_label(spec.provider)}" ;;' in text
        assert f'{index}) $(provider_label {spec.provider})' in text
        assert (
            f'{index}) ensure_provider_key "{spec.provider}"; '
            f'write_provider_config "{spec.provider}"'
        ) in text
        for key_name in spec.key_names:
            assert key_name in text
        assert spec.base_url in text
        assert spec.text_model in text
    assert f"{provider_start + len(PROVIDER_SPECS)}) Skip AI setup" in text
    assert "Paste API key now" in text
    assert text.count("has_tty || return 0") >= 2
    assert "save_secret_to_shell_profile" in text
    assert 'printf \'harness = "%s"\\n\' "$harness"' in text
    assert "Run $APP_NAME wizard now?" not in text
    assert '"$APP_NAME" wizard' not in text
    assert "If $APP_NAME helps you, star the GitHub repo now?" in text
    assert "gh repo star \"$REPO_SLUG\"" in text
    assert "api.github.com/user/starred/$REPO_SLUG" in text
    assert "Star it here: $REPO_URL" in text
    assert "Run: export PATH=" in text
    assert "pipx_bin_dir" in text
    assert "\\$PATH" in text
    assert "Run git-standup in your terminal to start the guided report builder." in text


def test_windows_installer_uses_numbered_ai_setup_and_key_entry() -> None:
    text = _read("install.ps1")

    assert "$MinimumPythonMajor = 3" in text
    assert "$MinimumPythonMinor = 10" in text
    assert "Test-PythonVersion" in text
    assert "Resolve-PythonExecutable" in text
    assert "Initialize-PipxBootstrap" in text
    assert "pipx-bootstrap" in text
    assert '"-m", "venv", $venvDir' in text
    assert '"install", "--upgrade", "pip", "pipx"' in text
    assert "$pipxExecutable = Get-Command pipx" in text
    assert "Ignoring broken pipx" in text
    assert "Install-OrUpdateApp" in text
    assert '"reinstall", $AppName, "--python", $Python' in text
    assert '"install", "--python", $Python, $RepoSpec' in text
    assert "Choose AI default:" in text
    for index, harness in enumerate(CLI_HARNESS_SPECS, start=1):
        assert f'"{harness.command}" {{ return "{harness.label}" }}' in text
        assert f'Write-Host "{index}) $(Get-HarnessLabel "{harness.command}")"' in text
        assert f'"{index}" {{ Save-HarnessConfig "{harness.command}"' in text
    provider_start = len(CLI_HARNESS_SPECS) + 1
    for index, spec in enumerate(PROVIDER_SPECS, start=provider_start):
        assert f'"{spec.provider}" {{ return "{_provider_label(spec.provider)}" }}' in text
        assert f'Write-Host "{index}) $(Get-ProviderLabel "{spec.provider}")"' in text
        assert f'"{index}" {{ Ensure-ProviderKey "{spec.provider}";' in text
        for key_name in spec.key_names:
            assert key_name in text
        assert spec.base_url in text
        assert spec.text_model in text
    assert f"{provider_start + len(PROVIDER_SPECS)}) Skip AI setup" in text
    assert "Paste API key now" in text
    assert "Save-UserSecret" in text
    assert '"harness = `"$Harness`""' in text
    assert "Run $AppName wizard now?" not in text
    assert "& $AppName wizard" not in text
    assert "If $AppName helps you, star the GitHub repo now?" in text
    assert "gh repo star $RepoSlug" in text
    assert "api.github.com/user/starred/$RepoSlug" in text
    assert "Star it here: $RepoUrl" in text
    assert "Get-PipxBinDir" in text
    assert "Run: `$env:Path" in text
    assert "Run git-standup in your terminal to start the guided report builder." in text


def test_ci_checks_installer_script_syntax() -> None:
    text = _read(".github/workflows/ci.yml")

    assert "bash -n install.sh" in text
    assert 'ParseFile("install.ps1"' in text


def test_ci_opts_into_current_node_runtime_for_actions() -> None:
    text = _read(".github/workflows/ci.yml")

    assert "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true" in text
    assert "uses: actions/checkout@v6" in text
    assert "uses: actions/setup-python@v6" in text
