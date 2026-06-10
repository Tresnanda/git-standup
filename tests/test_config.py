from pathlib import Path

import pytest

from git_standup.config import (
    AIConfig,
    config_path,
    format_config,
    load_config,
    parse_config_text,
    reset_config,
    save_config,
)


def test_parse_config_supports_author_alias_section() -> None:
    config = parse_config_text(
        'provider = "openai"\n'
        '\n'
        '[author_aliases]\n'
        '"Alice Example" = ["alice@example.com", "Alice E."]\n'
        'Bob = "bob@example.com|Bobby"\n'
    )

    assert config.provider == "openai"
    assert config.author_aliases == {
        "Alice Example": ("alice@example.com", "Alice E."),
        "Bob": ("bob@example.com", "Bobby"),
    }


def test_config_path_uses_xdg_config_home(tmp_path: Path) -> None:
    path = config_path(env={"XDG_CONFIG_HOME": str(tmp_path)}, home=tmp_path / "home")

    assert path == tmp_path / "git-standup" / "config.toml"


def test_config_round_trip_without_storing_secrets(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = AIConfig(
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-3.5-flash",
        author_aliases={"Alice Example": ("alice@example.com", "Alice E.")},
    )

    save_config(path, config)
    text = path.read_text(encoding="utf-8")

    assert "api_key" not in text
    assert '[author_aliases]' in text
    assert '"Alice Example" = ["alice@example.com", "Alice E."]' in text
    assert load_config(path) == config


def test_parse_config_rejects_secret_fields() -> None:
    with pytest.raises(ValueError, match="Secrets do not belong"):
        parse_config_text('provider = "openai"\napi_key = "sk-secret"\n')


def test_format_config_can_store_cli_harness_choice() -> None:
    text = format_config(AIConfig(harness="ollama", model="llama3.1"))

    assert 'harness = "ollama"' in text
    assert 'model = "llama3.1"' in text


def test_reset_config_removes_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('provider = "openai"\n', encoding="utf-8")

    assert reset_config(path) is True
    assert reset_config(path) is False
