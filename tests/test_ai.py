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
