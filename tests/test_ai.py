import json

from git_standup.ai import (
    _build_prompt,
    _chat_completions_url,
    _harness_command,
    generate_standup,
)


class _RecordingClient:
    calls = []

    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeCompletionResponse()


class _FakeCompletionResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self):
        return {"choices": [{"message": {"content": "Standup summary"}}]}


def test_build_prompt_includes_budget_metadata() -> None:
    metadata = {
        "truncated": True,
        "limits": {"max_commits": 2, "max_files_per_commit": 1},
        "commits_included": 2,
        "commits_truncated": True,
        "more_commits_available": True,
        "files_truncated": False,
        "commits_with_files_truncated": 0,
        "files_omitted": 0,
    }

    prompt = _build_prompt({"Alice": {}}, budget_metadata=metadata)

    assert "TRUNCATION METADATA" in prompt
    assert json.dumps(metadata, indent=2) in prompt
    assert "avoid inferring details from omitted commits/files" in prompt


def test_build_prompt_defaults_to_plain_text() -> None:
    prompt = _build_prompt({"Alice": {}})

    assert "Do NOT use Markdown syntax" in prompt


def test_build_prompt_requests_markdown_when_asked() -> None:
    prompt = _build_prompt({"Alice": {}}, output_format="markdown")

    assert "Format the summary as Markdown" in prompt
    assert "Do NOT use Markdown syntax" not in prompt


def test_build_prompt_warns_not_to_embellish_low_signal_commits() -> None:
    prompt = _build_prompt(
        {
            "Alice": {
                "2026-03-10": {
                    "commits": [
                        {
                            "hash": "abc123",
                            "subject": "wip",
                            "quality": {"signal": "low"},
                        }
                    ]
                }
            }
        }
    )

    assert "Do not embellish weak git evidence" in prompt
    assert "When a commit has `quality.signal` set to `low`" in prompt
    assert "say the commit message was vague instead of" in prompt


def test_generate_standup_uses_api_key_header_for_azure_deployments(monkeypatch) -> None:
    _RecordingClient.calls = []
    monkeypatch.setattr("git_standup.ai.httpx.Client", _RecordingClient)
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-06-01")

    result = generate_standup(
        {"Alice": {}},
        api_key="test-api-key",
        base_url="https://example.openai.azure.com/openai/deployments/standup-gpt",
    )

    assert result == "Standup summary"
    assert len(_RecordingClient.calls) == 1
    request = _RecordingClient.calls[0]
    assert request["url"] == (
        "https://example.openai.azure.com/openai/deployments/standup-gpt"
        "/chat/completions?api-version=2024-06-01"
    )
    assert request["headers"]["api-key"] == "test-api-key"
    assert "Authorization" not in request["headers"]


def test_generate_standup_keeps_bearer_auth_for_openai_compatible_providers(monkeypatch) -> None:
    _RecordingClient.calls = []
    monkeypatch.setattr("git_standup.ai.httpx.Client", _RecordingClient)

    result = generate_standup(
        {"Alice": {}},
        api_key="test-api-key",
        base_url="https://openai-compatible.example/v1",
    )

    assert result == "Standup summary"
    assert len(_RecordingClient.calls) == 1
    request = _RecordingClient.calls[0]
    assert request["url"] == "https://openai-compatible.example/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-api-key"
    assert "api-key" not in request["headers"]


def test_chat_completions_url_supports_azure_deployment_urls(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-06-01")

    url = _chat_completions_url(
        "https://example.openai.azure.com/openai/deployments/standup-gpt"
    )

    assert (
        url
        == "https://example.openai.azure.com/openai/deployments/standup-gpt"
        "/chat/completions?api-version=2024-06-01"
    )


def test_chat_completions_url_keeps_openai_compatible_base_url() -> None:
    assert _chat_completions_url("https://api.openai.com/v1") == (
        "https://api.openai.com/v1/chat/completions"
    )


def test_harness_command_builds_headless_invocations() -> None:
    prompt = "Summarize commits"
    prompt_file = "/tmp/git-standup-prompt.txt"

    assert _harness_command(
        harness="opencode",
        model="qwen/code",
        prompt=prompt,
        prompt_file=prompt_file,
    ) == ["opencode", "-p", prompt, "-q", "--model", "qwen/code"]
    assert _harness_command(
        harness="cursor-agent",
        model="gpt-5.2",
        prompt=prompt,
        prompt_file=prompt_file,
    ) == ["cursor-agent", "-p", "--output-format", "text", prompt, "--model", "gpt-5.2"]
    assert _harness_command(
        harness="aider",
        model="",
        prompt=prompt,
        prompt_file=prompt_file,
    ) == ["aider", "--message-file", prompt_file]
    assert _harness_command(
        harness="goose",
        model="",
        prompt=prompt,
        prompt_file=prompt_file,
    ) == [
        "goose",
        "run",
        "--no-session",
        "-i",
        prompt_file,
        "-q",
        "--output-format",
        "text",
    ]
