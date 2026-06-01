from git_standup.ai_env import (
    detect_ai_environment,
    mask_secret,
    resolve_ai_connection,
)
from git_standup.config import AIConfig


def test_mask_secret_keeps_values_private() -> None:
    assert mask_secret("sk-1234567890") == "sk-1...7890"
    assert mask_secret("short") == "set"


def test_detect_ai_environment_finds_keys_and_cli_harnesses() -> None:
    env = {
        "OPENROUTER_API_KEY": "sk-openrouter",
        "GEMINI_API_KEY": "sk-gemini",
        "ANTHROPIC_API_KEY": "sk-claude",
    }

    report = detect_ai_environment(
        env=env,
        which=lambda command: f"/usr/bin/{command}" if command in {"ollama", "codex"} else None,
    )

    assert [item["name"] for item in report["api_keys"]] == [
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
    ]
    assert "ANTHROPIC_API_KEY" not in {item["name"] for item in report["api_keys"]}
    assert report["cli_harnesses"] == ["codex", "ollama"]


def test_resolve_ai_connection_prefers_explicit_values() -> None:
    connection = resolve_ai_connection(
        api_key_arg="explicit-key",
        base_url_arg="https://example.test/v1",
        model_arg="custom-model",
        env={"OPENAI_API_KEY": "env-key"},
    )

    assert connection.provider == "custom"
    assert connection.api_key == "explicit-key"
    assert connection.base_url == "https://example.test/v1"
    assert connection.model == "custom-model"


def test_resolve_ai_connection_supports_openrouter_and_gemini_keys() -> None:
    openrouter = resolve_ai_connection(
        api_key_arg=None,
        base_url_arg=None,
        model_arg=None,
        env={"OPENROUTER_API_KEY": "sk-openrouter"},
    )
    gemini = resolve_ai_connection(
        api_key_arg=None,
        base_url_arg=None,
        model_arg=None,
        env={"GEMINI_API_KEY": "sk-gemini"},
    )

    assert openrouter.provider == "openrouter"
    assert openrouter.base_url == "https://openrouter.ai/api/v1"
    assert openrouter.model == "openai/gpt-4o-mini"
    assert gemini.provider == "gemini"
    assert gemini.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert gemini.model == "gemini-3.5-flash"


def test_resolve_ai_connection_uses_config_before_detected_environment() -> None:
    connection = resolve_ai_connection(
        api_key_arg=None,
        base_url_arg=None,
        model_arg=None,
        env={"OPENAI_API_KEY": "sk-openai", "GEMINI_API_KEY": "sk-gemini"},
        config=AIConfig(
            provider="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            model="gemini-3.5-flash",
        ),
    )

    assert connection.provider == "gemini"
    assert connection.api_key == "sk-gemini"
    assert connection.model == "gemini-3.5-flash"


def test_resolve_ai_connection_prefers_cli_model_over_config() -> None:
    connection = resolve_ai_connection(
        api_key_arg=None,
        base_url_arg=None,
        model_arg="manual-model",
        env={"OPENROUTER_API_KEY": "sk-openrouter"},
        config=AIConfig(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            model="saved-model",
        ),
    )

    assert connection.model == "manual-model"
