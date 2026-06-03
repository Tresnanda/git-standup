import json

from git_standup.ai import _build_prompt


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
