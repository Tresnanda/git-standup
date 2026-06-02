"""AI integration — OpenAI-compatible chat completion for standup generation."""

import json
import os
import subprocess
import tempfile
from typing import Any

import httpx

# Maximum characters for the prompt — conservative for small models
_MAX_PROMPT_CHARS = 120_000


def _build_prompt(
    commit_data: dict[str, Any],
    style: str = "standup",
    budget_metadata: dict[str, Any] | None = None,
) -> str:
    """Build a structured prompt from commit data."""
    metadata_section = ""
    if budget_metadata is not None:
        metadata_section = f"""

TRUNCATION METADATA:
{json.dumps(budget_metadata, indent=2, default=str)}

If truncated is true, explicitly note that the summary is based on a budgeted
subset of the git data and avoid inferring details from omitted commits/files."""

    return f"""You are a helpful assistant that generates a concise weekly
standup summary from git commit data.

Below is structured commit data grouped by author, then by date. Each commit
includes the hash, date, subject, body, and files changed with
insertions/deletions.

Generate a natural-language standup summary in a professional, friendly tone.
Use bullet points or short paragraphs. Include:
- What work was done (organized by author/project area)
- Notable changes or feature work
- Bug fixes or maintenance
{metadata_section}

COMMIT DATA:
{json.dumps(commit_data, indent=2, default=str)}

Generate the standup summary now:"""


def generate_standup(
    commit_data: dict[str, Any],
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
    budget_metadata: dict[str, Any] | None = None,
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
    url = f"{url_base.rstrip('/')}/chat/completions"

    prompt = _build_prompt(commit_data, budget_metadata=budget_metadata)

    # Truncate prompt if too long
    if len(prompt) > _MAX_PROMPT_CHARS:
        prompt = prompt[:_MAX_PROMPT_CHARS] + "\n\n[truncated due to length]"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

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
) -> str:
    """Generate a standup summary with a local AI CLI harness."""
    if harness != "codex":
        raise RuntimeError(f"Unsupported AI CLI harness: {harness}")

    prompt = (
        _build_prompt(commit_data, budget_metadata=budget_metadata)
        + "\n\nReturn only the finished standup summary. Do not edit files or run commands."
    )
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
