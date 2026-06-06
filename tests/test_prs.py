import json
import subprocess
from typing import Any

from git_standup.prs import _slug_from_remote_url, enrich_commits_with_prs


def test_slug_from_github_remote_urls() -> None:
    assert (
        _slug_from_remote_url("git@github.com:Tresnanda/git-standup.git")
        == "Tresnanda/git-standup"
    )
    assert (
        _slug_from_remote_url("https://github.com/Tresnanda/git-standup.git")
        == "Tresnanda/git-standup"
    )
    assert (
        _slug_from_remote_url("ssh://git@github.com/Tresnanda/git-standup.git")
        == "Tresnanda/git-standup"
    )
    assert (
        _slug_from_remote_url("https://github.com/Tresnanda/my.tool.git")
        == "Tresnanda/my.tool"
    )
    assert _slug_from_remote_url("https://example.com/Tresnanda/git-standup.git") is None


def test_include_prs_uses_local_merge_metadata_without_gh(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="https://github.com/Tresnanda/git-standup.git\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("git_standup.prs.shutil.which", lambda _name: None)

    commits = [
        {
            "hash": "abc123",
            "subject": "Merge pull request #42 from Tresnanda/pr-aware",
            "body": "Add PR-aware standup digest\n\nMore details",
        }
    ]

    enriched = enrich_commits_with_prs(commits, repo_path="/repo", query_github=False)

    assert calls == [["git", "-C", "/repo", "remote", "get-url", "origin"]]
    assert enriched[0]["pull_request"] == {
        "number": 42,
        "title": "Add PR-aware standup digest",
        "url": "https://github.com/Tresnanda/git-standup/pull/42",
        "source": "local",
    }
    assert "pull_request" not in commits[0]


def test_include_prs_uses_github_cli_for_squash_commit_title_and_url(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:3] == ["git", "-C", "/repo"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="git@github.com:Tresnanda/git-standup.git\n",
                stderr="",
            )
        if cmd[:3] == ["/usr/bin/gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "number": 17,
                        "title": "Ship polished PR title",
                        "url": "https://github.com/Tresnanda/git-standup/pull/17",
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "git_standup.prs.shutil.which",
        lambda name: "/usr/bin/gh" if name == "gh" else None,
    )

    commits = [{"hash": "abc123", "subject": "Initial local title (#17)", "body": ""}]

    enriched = enrich_commits_with_prs(commits, repo_path="/repo", query_github=True)

    assert calls[1] == [
        "/usr/bin/gh",
        "pr",
        "view",
        "17",
        "--repo",
        "Tresnanda/git-standup",
        "--json",
        "number,title,url",
    ]
    assert enriched[0]["pull_request"] == {
        "number": 17,
        "title": "Ship polished PR title",
        "url": "https://github.com/Tresnanda/git-standup/pull/17",
        "source": "github-cli",
    }


def test_pr_status_uses_github_cli_for_workflow_metadata(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:3] == ["git", "-C", "/repo"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="https://github.com/Tresnanda/git-standup.git\n",
                stderr="",
            )
        if cmd[:3] == ["/usr/bin/gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "number": 17,
                        "title": "Ship status board",
                        "url": "https://github.com/Tresnanda/git-standup/pull/17",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergeStateStatus": "CLEAN",
                        "reviewDecision": "APPROVED",
                        "labels": [{"name": "release"}],
                        "closingIssuesReferences": [
                            {
                                "number": 5,
                                "title": "Track rollout",
                                "url": "https://github.com/Tresnanda/git-standup/issues/5",
                            }
                        ],
                        "statusCheckRollup": [
                            {"name": "Python 3.11", "status": "COMPLETED", "conclusion": "SUCCESS"},
                            {"name": "Python 3.12", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        ],
                        "updatedAt": "2026-03-10T09:15:00Z",
                        "author": {"login": "alice"},
                        "assignees": [{"login": "release-owner"}],
                        "reviewRequests": [{"login": "reviewer"}],
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "git_standup.prs.shutil.which",
        lambda name: "/usr/bin/gh" if name == "gh" else None,
    )

    enriched = enrich_commits_with_prs(
        [{"hash": "abc123", "subject": "Initial local title (#17)", "body": ""}],
        repo_path="/repo",
        query_github=True,
        include_status=True,
    )

    assert calls[1] == [
        "/usr/bin/gh",
        "pr",
        "view",
        "17",
        "--repo",
        "Tresnanda/git-standup",
        "--json",
        (
            "number,title,url,state,isDraft,mergeStateStatus,reviewDecision,labels,"
            "closingIssuesReferences,statusCheckRollup,updatedAt,author,assignees,reviewRequests"
        ),
    ]
    assert enriched[0]["pull_request"] == {
        "number": 17,
        "title": "Ship status board",
        "url": "https://github.com/Tresnanda/git-standup/pull/17",
        "source": "github-cli",
        "state": "open",
        "draft": False,
        "merge_state_status": "clean",
        "review_decision": "approved",
        "labels": ["release"],
        "linked_issues": [
            {
                "number": 5,
                "title": "Track rollout",
                "url": "https://github.com/Tresnanda/git-standup/issues/5",
            }
        ],
        "checks": {
            "state": "passed",
            "total": 2,
            "passed": 2,
            "failed": 0,
            "pending": 0,
            "names": ["Python 3.11", "Python 3.12"],
        },
        "updated_at": "2026-03-10T09:15:00Z",
        "author": "alice",
        "assignees": ["release-owner"],
        "review_requests": ["reviewer"],
    }


def test_include_prs_can_find_pr_by_commit_hash_with_github_cli(monkeypatch) -> None:
    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["git", "-C", "/repo"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="https://github.com/Tresnanda/git-standup.git\n",
                stderr="",
            )
        if cmd[:3] == ["/usr/bin/gh", "pr", "list"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    [
                        {
                            "number": 28,
                            "title": "Find PR by SHA",
                            "url": "https://github.com/Tresnanda/git-standup/pull/28",
                        }
                    ]
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "git_standup.prs.shutil.which",
        lambda name: "/usr/bin/gh" if name == "gh" else None,
    )

    enriched = enrich_commits_with_prs(
        [{"hash": "abc123", "subject": "Add digest support", "body": ""}],
        repo_path="/repo",
        query_github=True,
    )

    assert enriched[0]["pull_request"]["number"] == 28
    assert enriched[0]["pull_request"]["title"] == "Find PR by SHA"
    assert enriched[0]["pull_request"]["source"] == "github-cli"
