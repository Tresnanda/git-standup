"""AI integration — OpenAI-compatible chat completion for standup generation."""

import json
import os
import re
import shlex
import subprocess
import tempfile
from typing import Any

import httpx

# Maximum characters for the prompt — conservative for small models
_MAX_PROMPT_CHARS = 120_000

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_FRAGMENT = (
    r"api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|token|password|passwd|pwd|secret|client[_-]?secret|"
    r"private[_-]?key"
)
_SENSITIVE_KEY_NAME_RE = re.compile(
    rf"(?:^|[_\-.])(?:{_SENSITIVE_KEY_FRAGMENT})(?:$|[_\-.])",
    re.IGNORECASE,
)
_SENSITIVE_KEY_VALUE_RE = re.compile(
    rf"\b(?P<key>[A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY_FRAGMENT})[A-Za-z0-9_.-]*)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?:"
    r"(?P<quote>['\"])(?P<quoted_value>.*?)(?P=quote)"
    r"|(?P<value>[^\s,'\";)}\]]+)"
    r")",
    re.IGNORECASE,
)
_URL_USERINFO_RE = re.compile(
    r"\b(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@(?P<host>[^/\s]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(
    r"\b(?P<prefix>Bearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_COMMON_SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk(?:_live|_test|_proj)?[_-][A-Za-z0-9._-]{6,}|"
    r"sk-" r"ant-api03-[A-Za-z0-9._-]{10,}|"
    r"sk-or-v1-[A-Za-z0-9._-]{10,}|"
    r"ghp_[A-Za-z0-9._-]{6,}|"
    r"github_pat_[A-Za-z0-9._-]{10,}|"
    r"glpat-[A-Za-z0-9._-]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_-]{20,}|"
    r"ya29\.[0-9A-Za-z_-]{20,}|"
    r"hf_[A-Za-z0-9]{10,}|"
    r"gsk_[A-Za-z0-9]{10,}|"
    r"xai-[A-Za-z0-9_-]{10,})\b"
)


def _is_azure_deployment_url(url_base: str) -> bool:
    """Return whether the URL targets an Azure OpenAI deployment endpoint."""
    return "/openai/deployments/" in url_base.rstrip("/").lower()


def _chat_completions_url(url_base: str) -> str:
    """Build a chat completions URL for OpenAI-compatible and Azure deployments."""
    base = url_base.rstrip("/")
    if not _is_azure_deployment_url(base):
        return f"{base}/chat/completions"

    if "/chat/completions" not in base:
        base = f"{base}/chat/completions"
    if "?" in base:
        return base

    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
    return f"{base}?api-version={api_version}"


def _request_headers(api_key: str, url_base: str) -> dict[str, str]:
    """Build provider-aware auth headers for chat completion requests."""
    headers = {"Content-Type": "application/json"}
    if _is_azure_deployment_url(url_base):
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _format_instruction(output_format: str) -> str:
    """Return the formatting directive for the requested output format."""
    if output_format == "markdown":
        return (
            "Format the summary as Markdown: use `##` headings, bullet lists, and "
            "`**bold**` for emphasis so it is paste-ready for Slack, Notion, or GitHub."
        )
    return (
        "Write in plain text using short paragraphs or simple dash bullets. "
        "Do NOT use Markdown syntax (no #, *, or backticks)."
    )


def _is_sensitive_key_name(value: str) -> bool:
    """Return True when a mapping key is a conventional secret-bearing name."""
    return bool(_SENSITIVE_KEY_NAME_RE.search(value))


def _redact_secret_text(value: str) -> str:
    """Mask API-key/token/password-looking values while retaining surrounding context."""

    def replace_key_value(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{match.group('key')}{match.group('sep')}{quote}{_REDACTED}{quote}"

    value = _URL_USERINFO_RE.sub(
        lambda match: f"{match.group('scheme')}{_REDACTED}@{match.group('host')}",
        value,
    )
    value = _SENSITIVE_KEY_VALUE_RE.sub(replace_key_value, value)
    value = _BEARER_TOKEN_RE.sub(lambda match: match.group("prefix") + _REDACTED, value)
    return _COMMON_SECRET_TOKEN_RE.sub(_REDACTED, value)


def _redact_mapping_key(value: Any) -> Any:
    """Preserve mapping keys except when the key text itself contains a secret value."""
    if isinstance(value, str):
        return _redact_secret_text(value)
    return value


def _redact_for_ai_prompt(value: Any, *, key_name: str | None = None) -> Any:
    """Return a copy of prompt data with secret-looking strings redacted."""
    if isinstance(value, str):
        if key_name is not None and _is_sensitive_key_name(key_name):
            return _REDACTED
        return _redact_secret_text(value)
    if isinstance(value, list):
        return [_redact_for_ai_prompt(item, key_name=key_name) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_for_ai_prompt(item, key_name=key_name) for item in value)
    if isinstance(value, dict):
        return {
            _redact_mapping_key(key): _redact_for_ai_prompt(
                item,
                key_name=key if isinstance(key, str) else None,
            )
            for key, item in value.items()
        }
    return value


def _build_prompt(
    commit_data: dict[str, Any],
    style: str = "standup",
    budget_metadata: dict[str, Any] | None = None,
    output_format: str = "text",
) -> str:
    """Build a structured prompt from commit data."""
    redacted_commit_data = _redact_for_ai_prompt(commit_data)
    metadata_section = ""
    if budget_metadata is not None:
        redacted_budget_metadata = _redact_for_ai_prompt(budget_metadata)
        metadata_section = f"""

TRUNCATION METADATA:
{json.dumps(redacted_budget_metadata, indent=2, default=str)}

If truncated is true, explicitly note that the summary is based on a budgeted
subset of the git data and avoid inferring details from omitted commits/files."""

    return f"""You are a helpful assistant that generates a concise weekly
standup summary from git commit data.

Below is structured commit data grouped by author, then by date. Each commit
includes the hash, date, subject, body, and files changed with
insertions/deletions.

Generate a natural-language standup summary in a professional, friendly tone.
{_format_instruction(output_format)} Include:
- What work was done (organized by author/project area)
- Notable changes or feature work
- Bug fixes or maintenance

Evidence rules:
- Do not embellish weak git evidence into impressive-sounding accomplishments.
- When a commit has `quality.signal` set to `low`, treat it as a low-signal or
  placeholder commit message. Summarize only concrete file/body evidence and,
  if there is not enough evidence, say the commit message was vague instead of
  inventing intent.
- Vague subjects like "wip", "fix", "update", "changes", "misc", or "tmp"
  should not be rewritten as polished outcomes unless the body/files clearly
  support that outcome.
{metadata_section}

COMMIT DATA:
{json.dumps(redacted_commit_data, indent=2, default=str)}

Generate the standup summary now:"""


def generate_standup(
    commit_data: dict[str, Any],
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
    budget_metadata: dict[str, Any] | None = None,
    output_format: str = "text",
) -> str:
    """Send commit data to an LLM and return a natural-language standup summary.

    Supports any OpenAI-compatible API endpoint.

    Args:
        commit_data: Structured commit data (dict with authors/dates/commits).
        api_key: API key. Falls back to OPENAI_API_KEY env var if None.
        model: Model name (default: gpt-4o-mini).
        base_url: API base URL. Falls back to OPENAI_BASE_URL env var,
                  then defaults to OpenAI's API.

    Returns:
        Generated standup text.

    Raises:
        RuntimeError: on API errors or missing API key.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "No API key provided. Use --api-key or set OPENAI_API_KEY environment variable."
        )

    url_base = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    url = _chat_completions_url(url_base)

    prompt = _build_prompt(
        commit_data, budget_metadata=budget_metadata, output_format=output_format
    )

    # Truncate prompt if too long
    if len(prompt) > _MAX_PROMPT_CHARS:
        prompt = prompt[:_MAX_PROMPT_CHARS] + "\n\n[truncated due to length]"

    headers = _request_headers(key, url_base)

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text[:500]
        raise RuntimeError(
            f"API request failed (HTTP {exc.response.status_code}): {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc

    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("API response had no choices")

    content = choices[0].get("message", {}).get("content", "")
    return content.strip()


def generate_standup_with_harness(
    commit_data: dict[str, Any],
    harness: str,
    model: str = "",
    budget_metadata: dict[str, Any] | None = None,
    output_format: str = "text",
) -> str:
    """Generate a standup summary with a local AI CLI harness."""
    prompt = (
        _build_prompt(
            commit_data, budget_metadata=budget_metadata, output_format=output_format
        )
        + "\n\nReturn only the finished standup summary. Do not edit files or run commands."
    )
    if harness != "codex":
        return _run_non_codex_harness(harness=harness, model=model, prompt=prompt)

    command = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
    ]
    if model:
        command.extend(["--model", model])

    with tempfile.NamedTemporaryFile(suffix=".txt") as output_file:
        command.extend(["--output-last-message", output_file.name, "-"])
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Codex CLI was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Codex CLI timed out while generating the standup.") from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Codex CLI failed: {detail}")

        output = output_file.read().decode("utf-8").strip()
        return output or result.stdout.strip()


def _run_non_codex_harness(*, harness: str, model: str, prompt: str) -> str:
    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8") as prompt_file,
        tempfile.TemporaryDirectory(prefix="git-standup-ai-") as workdir,
    ):
        prompt_file.write(prompt)
        prompt_file.flush()
        command = _harness_command(
            harness=harness,
            model=model,
            prompt=prompt,
            prompt_file=prompt_file.name,
        )
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                cwd=workdir,
                timeout=180,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"{harness} CLI was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{harness} CLI timed out while generating the standup.") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{harness} CLI failed: {detail}")
    return result.stdout.strip()


def _harness_command(
    *,
    harness: str,
    model: str,
    prompt: str,
    prompt_file: str,
) -> list[str]:
    if harness == "opencode":
        command = ["opencode", "-p", prompt, "-q"]
        if model:
            command.extend(["--model", model])
        return command
    if harness in {"cursor-agent", "agent"}:
        command = [harness, "-p", "--output-format", "text", prompt]
        if model:
            command.extend(["--model", model])
        return command
    if harness == "gemini":
        command = ["gemini", "-p", prompt]
        if model:
            command.extend(["--model", model])
        return command
    if harness == "aider":
        command = ["aider", "--message-file", prompt_file]
        if model:
            command.extend(["--model", model])
        return command
    if harness == "goose":
        command = [
            "goose",
            "run",
            "--no-session",
            "-i",
            prompt_file,
            "-q",
            "--output-format",
            "text",
        ]
        if model:
            command.extend(["--model", model])
        return command
    if harness == "copilot":
        return ["copilot", "-p", prompt]
    if harness == "kiro-cli":
        return ["kiro-cli", "chat", "--no-interactive", prompt]
    if harness == "amp":
        return ["amp", "-x", prompt]
    raise RuntimeError(
        "Unsupported AI CLI harness: "
        + shlex.quote(harness)
        + ". Run git-standup config set-cli with a supported harness."
    )
