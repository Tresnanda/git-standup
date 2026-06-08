import json
from typing import Any

import pytest

from git_standup import github_api
from git_standup.github_api import GitHubApiRunCache, GitHubRateLimitError, get_remote_commits


def _api_commit(sha: str, *, message: str | None = None) -> dict[str, object]:
    return {
        "sha": sha,
        "parents": [{"sha": "parent"}],
        "author": {"login": "alicehub"},
        "commit": {
            "author": {
                "name": "Alice",
                "email": "alice@example.com",
                "date": "2026-03-10T09:15:00Z",
            },
            "message": message or f"Commit {sha}",
        },
    }


def test_get_remote_commits_reuses_per_run_api_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        calls.append(endpoint)
        if endpoint == "/repos/Tresnanda/api/commits":
            assert params is not None
            return [_api_commit("abc123")]
        if endpoint == "/repos/Tresnanda/api/commits/abc123":
            return {
                **_api_commit("abc123", message="Cached details"),
                "files": [{"filename": "src/app.py", "additions": 3, "deletions": 1}],
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}; headers={headers}")

    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)
    cache = GitHubApiRunCache()

    first = get_remote_commits("Tresnanda/api", since="2026-03-01", cache=cache)
    second = get_remote_commits("Tresnanda/api", since="2026-03-01", cache=cache)

    assert calls == ["/repos/Tresnanda/api/commits", "/repos/Tresnanda/api/commits/abc123"]
    assert first == second
    assert second[0]["files"] == [{"path": "src/app.py", "insertions": 3, "deletions": 1}]
    assert cache.hits == 2
    assert cache.misses == 2
    assert cache.metadata() == {
        "cache": {"hits": 2, "misses": 2},
        "rate_limit": {
            "limited": False,
            "commit_detail_requests_skipped": 0,
            "pull_request_enrichments_skipped": 0,
        },
    }


def test_get_remote_commits_skips_progressive_enrichment_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        del params, headers
        calls.append(endpoint)
        if endpoint == "/repos/Tresnanda/api/commits":
            return [_api_commit("abc123"), _api_commit("def456")]
        if endpoint == "/repos/Tresnanda/api/commits/abc123":
            raise GitHubRateLimitError("API rate limit exceeded")
        raise AssertionError(f"unexpected endpoint after rate limit: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)
    cache = GitHubApiRunCache()

    commits = get_remote_commits(
        "Tresnanda/api",
        since="2026-03-01",
        include_prs=True,
        cache=cache,
    )

    assert calls == ["/repos/Tresnanda/api/commits", "/repos/Tresnanda/api/commits/abc123"]
    assert [commit["hash"] for commit in commits] == ["abc123", "def456"]
    for commit in commits:
        assert commit["files"] == []
        assert commit["github_api"] == {
            "commit_detail": {"skipped": True, "reason": "rate_limit"},
            "pull_request_enrichment": {"skipped": True, "reason": "rate_limit"},
        }
    assert cache.metadata() == {
        "cache": {"hits": 0, "misses": 2},
        "rate_limit": {
            "limited": True,
            "commit_detail_requests_skipped": 2,
            "pull_request_enrichments_skipped": 2,
        },
        "repositories": {
            "Tresnanda/api": {
                "commit_details_skipped": 2,
                "pull_request_enrichments_skipped": 2,
                "rate_limited": True,
            }
        },
    }


def test_get_remote_commits_uses_cached_detail_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        del params, headers
        calls.append(endpoint)
        if endpoint == "/repos/Tresnanda/api/commits":
            return [_api_commit("abc123")]
        if endpoint == "/repos/Tresnanda/api/commits/abc123":
            return {
                **_api_commit("abc123", message="Cached details"),
                "files": [{"filename": "src/app.py", "additions": 3, "deletions": 1}],
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)
    cache = GitHubApiRunCache()

    first = get_remote_commits("Tresnanda/api", since="2026-03-01", cache=cache)
    cache.mark_rate_limited("Tresnanda/api")
    second = get_remote_commits("Tresnanda/api", since="2026-03-01", cache=cache)

    assert calls == ["/repos/Tresnanda/api/commits", "/repos/Tresnanda/api/commits/abc123"]
    assert first == second
    assert second[0]["files"] == [{"path": "src/app.py", "insertions": 3, "deletions": 1}]
    assert "github_api" not in second[0]


def test_get_remote_commits_reuses_cached_default_window_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str | int] | None]] = []

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        del headers
        calls.append((endpoint, params))
        if endpoint == "/repos/Tresnanda/api/commits":
            return [_api_commit("abc123")]
        if endpoint == "/repos/Tresnanda/api/commits/abc123":
            return _api_commit("abc123")
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)
    cache = GitHubApiRunCache()

    first = get_remote_commits("Tresnanda/api", days=7, cache=cache)
    cache.mark_rate_limited("Tresnanda/api")
    second = get_remote_commits("Tresnanda/api", days=7, cache=cache)

    assert first == second
    assert len(calls) == 2


def test_get_remote_commits_skips_uncached_author_lookup_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        del params, headers
        raise AssertionError(f"unexpected uncached API call after rate limit: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)
    cache = GitHubApiRunCache(rate_limited=True)

    commits = get_remote_commits("Tresnanda/api", author="me", since="2026-03-01", cache=cache)

    assert commits == []
    assert cache.repositories["Tresnanda/api"]["rate_limited"] is True


def test_cli_api_mode_skips_uncached_repo_listing_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    from git_standup import cli

    calls: list[str] = []

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        del params, headers
        calls.append(endpoint)
        if endpoint == "/repos/Tresnanda/api/commits":
            return [_api_commit("abc123", message="Fallback summary")]
        if endpoint == "/repos/Tresnanda/api/commits/abc123":
            raise GitHubRateLimitError("secondary rate limit")
        raise AssertionError(f"unexpected endpoint after rate limit: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)

    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-repo",
            "http://github.com/Tresnanda/web.git",
            "--remote-backend",
            "api",
            "--json",
            "--since",
            "2026-03-01",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert set(output["_repositories"]) == {"Tresnanda/api"}
    repositories = output["_metadata"]["github_api"]["repositories"]
    assert repositories["Tresnanda/web"]["rate_limited"] is True
    assert calls == ["/repos/Tresnanda/api/commits", "/repos/Tresnanda/api/commits/abc123"]


def test_cli_json_includes_rate_limit_skip_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    from git_standup import cli

    def fail_clone(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("API backend must not clone remote repositories")

    def fake_gh_api_json(
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: list[str] | None = None,
    ) -> object:
        del params, headers
        if endpoint == "/repos/Tresnanda/api/commits":
            return [_api_commit("abc123", message="Fallback summary")]
        if endpoint == "/repos/Tresnanda/api/commits/abc123":
            raise GitHubRateLimitError("secondary rate limit")
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(cli, "_clone_remote_repo", fail_clone)
    monkeypatch.setattr(github_api, "_gh_api_json", fake_gh_api_json)

    exit_code = cli.main(
        [
            "--remote-repo",
            "Tresnanda/api",
            "--remote-backend",
            "api",
            "--json",
            "--since",
            "2026-03-01",
            "--include-prs",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    metadata = output["_metadata"]["github_api"]
    assert metadata["rate_limit"] == {
        "limited": True,
        "commit_detail_requests_skipped": 1,
        "pull_request_enrichments_skipped": 1,
    }
    commit = output["_repositories"]["Tresnanda/api"]["Alice"]["2026-03-10"]["commits"][0]
    assert commit["github_api"] == {
        "commit_detail": {"skipped": True, "reason": "rate_limit"},
        "pull_request_enrichment": {"skipped": True, "reason": "rate_limit"},
    }
