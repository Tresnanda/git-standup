"""AI integration — OpenAI-compatible chat completion for standup generation."""

import json
import os
import shlex
import subprocess
import tempfile
from copy import deepcopy
from typing import Any

import httpx

# Maximum characters for the prompt — conservative for small models
_MAX_PROMPT_CHARS = 120_000
_STRUCTURED_BUDGET_METADATA_KEY = "structured_prompt_budget"
_OMITTED_SAMPLE_LIMIT = 5


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


def _build_prompt(
    commit_data: dict[str, Any],
    style: str = "standup",
    budget_metadata: dict[str, Any] | None = None,
    output_format: str = "text",
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
{json.dumps(commit_data, indent=2, default=str)}

Generate the standup summary now:"""


def _build_budgeted_prompt(
    commit_data: dict[str, Any],
    *,
    budget_metadata: dict[str, Any] | None = None,
    output_format: str = "text",
    max_chars: int | None = None,
) -> str:
    """Build a prompt that fits max_chars by truncating structured commit data.

    The returned prompt always contains complete JSON. When the full prompt is too
    large, file lists are omitted first, then whole commits. Metadata records what
    was omitted so the model can avoid inferring from missing evidence.
    """
    if max_chars is None:
        max_chars = _MAX_PROMPT_CHARS
    prompt = _build_prompt(
        commit_data,
        budget_metadata=budget_metadata,
        output_format=output_format,
    )
    if len(prompt) <= max_chars:
        return prompt

    budgeted_data = deepcopy(commit_data)
    tracker = _new_budget_tracker()
    original_prompt_chars = len(prompt)
    prompt = _build_prompt(
        budgeted_data,
        budget_metadata=_with_structured_budget_metadata(
            budget_metadata,
            tracker,
            max_chars=max_chars,
            original_prompt_chars=original_prompt_chars,
        ),
        output_format=output_format,
    )

    while len(prompt) > max_chars and _omit_next_commit_files(budgeted_data, tracker):
        prompt = _build_prompt(
            budgeted_data,
            budget_metadata=_with_structured_budget_metadata(
                budget_metadata,
                tracker,
                max_chars=max_chars,
                original_prompt_chars=original_prompt_chars,
            ),
            output_format=output_format,
        )

    while len(prompt) > max_chars and _trim_omitted_samples(tracker):
        prompt = _build_prompt(
            budgeted_data,
            budget_metadata=_with_structured_budget_metadata(
                budget_metadata,
                tracker,
                max_chars=max_chars,
                original_prompt_chars=original_prompt_chars,
            ),
            output_format=output_format,
        )

    while len(prompt) > max_chars and _omit_next_commit(budgeted_data, tracker):
        prompt = _build_prompt(
            budgeted_data,
            budget_metadata=_with_structured_budget_metadata(
                budget_metadata,
                tracker,
                max_chars=max_chars,
                original_prompt_chars=original_prompt_chars,
            ),
            output_format=output_format,
        )

    while len(prompt) > max_chars and _trim_omitted_samples(tracker):
        prompt = _build_prompt(
            budgeted_data,
            budget_metadata=_with_structured_budget_metadata(
                budget_metadata,
                tracker,
                max_chars=max_chars,
                original_prompt_chars=original_prompt_chars,
            ),
            output_format=output_format,
        )

    return prompt


def _new_budget_tracker() -> dict[str, Any]:
    return {
        "repositories": [],
        "authors": [],
        "authors_by_repository": [],
        "commits": [],
        "files": [],
        "omitted_counts": {
            "repositories": 0,
            "authors": 0,
            "commits": 0,
            "files": 0,
        },
        "omitted_samples_truncated": False,
    }


def _with_structured_budget_metadata(
    budget_metadata: dict[str, Any] | None,
    tracker: dict[str, Any],
    *,
    max_chars: int,
    original_prompt_chars: int,
) -> dict[str, Any]:
    metadata = dict(budget_metadata or {})
    metadata["truncated"] = True
    metadata[_STRUCTURED_BUDGET_METADATA_KEY] = {
        "truncated": True,
        "max_prompt_chars": max_chars,
        "original_prompt_chars": original_prompt_chars,
        "omitted": {
            "repositories": list(tracker["repositories"]),
            "authors": list(tracker["authors"]),
            "authors_by_repository": list(tracker["authors_by_repository"]),
            "commits": list(tracker["commits"]),
            "files": list(tracker["files"]),
        },
        "omitted_counts": dict(tracker["omitted_counts"]),
        "omitted_samples_truncated": tracker["omitted_samples_truncated"],
    }
    return metadata


def _iter_day_locations(commit_data: dict[str, Any]):
    repositories = commit_data.get("_repositories")
    if isinstance(repositories, dict):
        for repo_name, repo_data in repositories.items():
            if isinstance(repo_data, dict):
                yield from _iter_repo_day_locations(
                    root=commit_data,
                    repo_name=repo_name,
                    repo_data=repo_data,
                )
        return

    yield from _iter_repo_day_locations(root=commit_data, repo_name=None, repo_data=commit_data)


def _iter_repo_day_locations(
    *,
    root: dict[str, Any],
    repo_name: str | None,
    repo_data: dict[str, Any],
):
    for author, author_data in repo_data.items():
        if str(author).startswith("_") or not isinstance(author_data, dict):
            continue
        for date_key, day_data in author_data.items():
            if isinstance(day_data, dict):
                yield {
                    "root": root,
                    "repo": repo_name,
                    "repo_data": repo_data,
                    "author": author,
                    "author_data": author_data,
                    "date": date_key,
                    "day_data": day_data,
                }


def _omit_next_commit_files(commit_data: dict[str, Any], tracker: dict[str, Any]) -> bool:
    for day_location in reversed(list(_iter_day_locations(commit_data))):
        commits = day_location["day_data"].get("commits", [])
        if not isinstance(commits, list):
            continue
        for commit in reversed(commits):
            files = commit.get("files", []) if isinstance(commit, dict) else []
            if not isinstance(files, list) or not files:
                continue
            for file_item in files:
                _record_omitted_file(tracker, day_location, commit, file_item)
            commit["files"] = []
            truncated = dict(commit.get("truncated", {}))
            truncated["files"] = True
            truncated["files_omitted"] = truncated.get("files_omitted", 0) + len(files)
            commit["truncated"] = truncated
            _recompute_day_stats(day_location["day_data"])
            return True
    return False


def _omit_next_commit(commit_data: dict[str, Any], tracker: dict[str, Any]) -> bool:
    for day_location in reversed(list(_iter_day_locations(commit_data))):
        commits = day_location["day_data"].get("commits", [])
        if not isinstance(commits, list) or not commits:
            continue
        commit = commits.pop()
        if isinstance(commit, dict):
            _record_omitted_commit(tracker, day_location, commit)
            files = commit.get("files", [])
            if isinstance(files, list):
                for file_item in files:
                    _record_omitted_file(tracker, day_location, commit, file_item)
        _recompute_day_stats(day_location["day_data"])
        _remove_empty_day_author_or_repo(day_location, tracker)
        return True
    return False


def _record_omitted_file(
    tracker: dict[str, Any],
    day_location: dict[str, Any],
    commit: dict[str, Any],
    file_item: Any,
) -> None:
    tracker["omitted_counts"]["files"] += 1
    if len(tracker["files"]) >= _OMITTED_SAMPLE_LIMIT:
        tracker["omitted_samples_truncated"] = True
        return

    path = file_item.get("path", str(file_item)) if isinstance(file_item, dict) else str(file_item)
    tracker["files"].append(
        {
            "repository": day_location["repo"],
            "author": day_location["author"],
            "date": day_location["date"],
            "commit": commit.get("hash", ""),
            "path": path,
        }
    )


def _record_omitted_commit(
    tracker: dict[str, Any],
    day_location: dict[str, Any],
    commit: dict[str, Any],
) -> None:
    tracker["omitted_counts"]["commits"] += 1
    if len(tracker["commits"]) >= _OMITTED_SAMPLE_LIMIT:
        tracker["omitted_samples_truncated"] = True
        return

    tracker["commits"].append(
        {
            "repository": day_location["repo"],
            "author": day_location["author"],
            "date": day_location["date"],
            "hash": commit.get("hash", ""),
            "subject": commit.get("subject", ""),
        }
    )


def _record_omitted_author(
    tracker: dict[str, Any],
    repo_name: str | None,
    author: str,
) -> None:
    if author not in tracker["authors"]:
        tracker["authors"].append(author)
        tracker["omitted_counts"]["authors"] += 1

    author_context = {"repository": repo_name, "author": author}
    if author_context not in tracker["authors_by_repository"]:
        tracker["authors_by_repository"].append(author_context)


def _record_omitted_repository(tracker: dict[str, Any], repo_name: str | None) -> None:
    if repo_name is None or repo_name in tracker["repositories"]:
        return
    tracker["repositories"].append(repo_name)
    tracker["omitted_counts"]["repositories"] += 1


def _remove_empty_day_author_or_repo(
    day_location: dict[str, Any], tracker: dict[str, Any]
) -> None:
    day_data = day_location["day_data"]
    commits = day_data.get("commits", [])
    if isinstance(commits, list) and commits:
        return

    author_data = day_location["author_data"]
    date_key = day_location["date"]
    if date_key in author_data:
        del author_data[date_key]

    if author_data:
        return

    repo_data = day_location["repo_data"]
    author = day_location["author"]
    if author in repo_data:
        del repo_data[author]
    _record_omitted_author(tracker, day_location["repo"], author)

    repo_name = day_location["repo"]
    repositories = day_location["root"].get("_repositories")
    if repo_name is not None and isinstance(repositories, dict) and not repo_data:
        del repositories[repo_name]
        _record_omitted_repository(tracker, repo_name)


def _recompute_day_stats(day_data: dict[str, Any]) -> None:
    commits = day_data.get("commits", [])
    if not isinstance(commits, list):
        return
    files_changed: list[str] = []
    total_insertions = 0
    total_deletions = 0
    total_files = 0
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        files = commit.get("files", [])
        if not isinstance(files, list):
            continue
        total_files += len(files)
        for file_item in files:
            if not isinstance(file_item, dict):
                continue
            path = file_item.get("path")
            if isinstance(path, str):
                files_changed.append(path)
            total_insertions += int(file_item.get("insertions") or 0)
            total_deletions += int(file_item.get("deletions") or 0)
    day_data["stats"] = {
        "total_commits": len(commits),
        "total_insertions": total_insertions,
        "total_deletions": total_deletions,
        "total_files": total_files,
        "files_changed": files_changed,
    }


def _trim_omitted_samples(tracker: dict[str, Any]) -> bool:
    trimmed = False
    for key in ("files", "commits"):
        if not tracker[key]:
            continue
        new_length = len(tracker[key]) // 2
        tracker[key] = tracker[key][:new_length]
        trimmed = True
    if trimmed:
        tracker["omitted_samples_truncated"] = True
    return trimmed


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

    prompt = _build_budgeted_prompt(
        commit_data,
        budget_metadata=budget_metadata,
        output_format=output_format,
    )

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
    suffix = "\n\nReturn only the finished standup summary. Do not edit files or run commands."
    prompt = (
        _build_budgeted_prompt(
            commit_data,
            budget_metadata=budget_metadata,
            output_format=output_format,
            max_chars=max(1, _MAX_PROMPT_CHARS - len(suffix)),
        )
        + suffix
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
