"""AI provider and local harness detection for installer and wizard flows."""

from __future__ import annotations

from dataclasses import dataclass
from shutil import which as default_which
from typing import Callable, Mapping

from git_standup.config import AIConfig


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    key_names: tuple[str, ...]
    base_url: str
    text_model: str
    vision: bool = False


@dataclass(frozen=True)
class AIConnection:
    provider: str
    api_key: str
    base_url: str
    model: str


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("openai", ("OPENAI_API_KEY",), "https://api.openai.com/v1", "gpt-4o-mini", True),
    ProviderSpec(
        "gemini",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-3.5-flash",
        True,
    ),
    ProviderSpec(
        "openrouter",
        ("OPENROUTER_API_KEY",),
        "https://openrouter.ai/api/v1",
        "openai/gpt-4o-mini",
        True,
    ),
    ProviderSpec(
        "groq",
        ("GROQ_API_KEY",),
        "https://api.groq.com/openai/v1",
        "llama-3.3-70b-versatile",
    ),
    ProviderSpec(
        "mistral",
        ("MISTRAL_API_KEY",),
        "https://api.mistral.ai/v1",
        "mistral-small-latest",
    ),
    ProviderSpec(
        "together",
        ("TOGETHER_API_KEY",),
        "https://api.together.xyz/v1",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ),
    ProviderSpec("perplexity", ("PERPLEXITY_API_KEY",), "https://api.perplexity.ai", "sonar-pro"),
    ProviderSpec("xai", ("XAI_API_KEY",), "https://api.x.ai/v1", "grok-3-mini"),
)

CLI_HARNESSES = ("openai", "gemini", "ollama", "lms", "gh", "vercel", "aider", "opencode", "codex")
LOCAL_ENDPOINTS = (
    "http://localhost:11434/v1",
    "http://localhost:1234/v1",
    "http://localhost:4000/v1",
)

_HARNESS_DEFAULTS = {
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
    "lms": ("http://localhost:1234/v1", "local-model"),
}


def mask_secret(value: str) -> str:
    """Return a display-safe representation of a secret."""
    if len(value) < 8:
        return "set"
    return f"{value[:4]}...{value[-4:]}"


def _iter_detected_keys(env: Mapping[str, str]) -> list[dict[str, object]]:
    detected: list[dict[str, object]] = []
    for spec in PROVIDER_SPECS:
        for key_name in spec.key_names:
            value = env.get(key_name)
            if value:
                detected.append(
                    {
                        "name": key_name,
                        "provider": spec.provider,
                        "masked": mask_secret(value),
                        "base_url": spec.base_url,
                        "model": spec.text_model,
                        "vision": spec.vision,
                    }
                )
                break

    azure_key = env.get("AZURE_OPENAI_API_KEY")
    if azure_key:
        detected.append(
            {
                "name": "AZURE_OPENAI_API_KEY",
                "provider": "azure-openai",
                "masked": mask_secret(azure_key),
                "base_url": env.get("AZURE_OPENAI_ENDPOINT", ""),
                "model": env.get("AZURE_OPENAI_DEPLOYMENT") or env.get("AZURE_OPENAI_MODEL", ""),
                "vision": False,
            }
        )
    return detected


def detect_ai_environment(
    env: Mapping[str, str],
    which: Callable[[str], str | None] = default_which,
) -> dict[str, object]:
    """Detect AI keys and local CLI harnesses without making paid API calls."""
    harnesses = sorted(command for command in CLI_HARNESSES if which(command))
    return {
        "api_keys": sorted(_iter_detected_keys(env), key=lambda item: str(item["name"])),
        "cli_harnesses": harnesses,
        "local_endpoints": list(LOCAL_ENDPOINTS),
    }


def _spec_for_provider(provider: str) -> ProviderSpec | None:
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec
    return None


def _api_key_for_provider(provider: str, env: Mapping[str, str]) -> str:
    spec = _spec_for_provider(provider)
    if spec:
        for key_name in spec.key_names:
            if env.get(key_name):
                return env[key_name]
    return env.get("OPENAI_API_KEY", "")


def resolve_ai_connection(
    api_key_arg: str | None,
    base_url_arg: str | None,
    model_arg: str | None,
    env: Mapping[str, str],
    config: AIConfig | None = None,
    provider_arg: str | None = None,
) -> AIConnection:
    """Resolve an OpenAI-compatible connection for text generation."""
    if provider_arg or (config and (config.provider or config.harness)):
        provider = provider_arg or (config.provider if config and config.provider else "")
        harness = config.harness if config else ""
        if harness and not provider:
            base_url, default_model = _HARNESS_DEFAULTS.get(
                harness,
                ("https://api.openai.com/v1", "gpt-4o-mini"),
            )
            return AIConnection(
                provider=harness,
                api_key=api_key_arg or env.get("OPENAI_API_KEY", "") or harness,
                base_url=base_url_arg or (config.base_url if config else "") or base_url,
                model=model_arg or (config.model if config else "") or default_model,
            )

        spec = _spec_for_provider(provider)
        default_base_url = spec.base_url if spec else "https://api.openai.com/v1"
        default_model = spec.text_model if spec else "gpt-4o-mini"
        if provider == "openai":
            default_base_url = env.get("OPENAI_BASE_URL") or default_base_url
        return AIConnection(
            provider=provider or "custom",
            api_key=api_key_arg or _api_key_for_provider(provider, env),
            base_url=base_url_arg or (config.base_url if config else "") or default_base_url,
            model=model_arg or (config.model if config else "") or default_model,
        )

    if api_key_arg:
        return AIConnection(
            provider="custom" if base_url_arg else "openai",
            api_key=api_key_arg,
            base_url=base_url_arg or env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            model=model_arg or "gpt-4o-mini",
        )

    if base_url_arg:
        return AIConnection(
            provider="custom",
            api_key=env.get("OPENAI_API_KEY", ""),
            base_url=base_url_arg,
            model=model_arg or "gpt-4o-mini",
        )

    for spec in PROVIDER_SPECS:
        for key_name in spec.key_names:
            value = env.get(key_name)
            if value:
                return AIConnection(
                    provider=spec.provider,
                    api_key=value,
                    base_url=env.get("OPENAI_BASE_URL") or spec.base_url
                    if spec.provider == "openai"
                    else spec.base_url,
                    model=model_arg or spec.text_model,
                )

    return AIConnection(
        provider="openai",
        api_key="",
        base_url=env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        model=model_arg or "gpt-4o-mini",
    )
