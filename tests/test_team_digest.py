import pytest

from git_standup import cli
from git_standup.formatter import (
    build_insights_output,
    build_team_digest_output,
    build_workflow_board_output,
)


def _commit(
    subject: str,
    *,
    hash_: str,
    author: str = "Alice",
    body: str = "",
    files: list[dict[str, object]] | None = None,
    pull_request: dict[str, object] | None = None,
    issues: list[dict[str, object]] | None = None,
    quality: dict[str, object] | None = None,
) -> dict[str, object]:
    commit: dict[str, object] = {
        "hash": hash_,
        "author_name": author,
        "author_email": f"{author.lower()}@example.com",
        "date": "2026-03-10T09:15:00+00:00",
        "subject": subject,
        "body": body,
        "files": files or [{"path": "src/app.py", "insertions": 5, "deletions": 1}],
    }
    if pull_request:
        commit["pull_request"] = pull_request
    if issues:
        commit["issues"] = issues
    if quality:
        commit["quality"] = quality
    return commit


def test_team_digest_formatter_groups_by_owner_and_surfaces_workflow_signals() -> None:
    commit_data = cli._build_commit_data(
        [
            _commit(
                "feat: ship team digest PROJ-7",
                hash_="fea111111",
                pull_request={
                    "number": 42,
                    "title": "Add team digest",
                    "url": "https://github.com/Tresnanda/git-standup/pull/42",
                },
                issues=[{"id": "PROJ-7", "url": "https://jira.example/browse/PROJ-7"}],
            ),
            _commit("WIP auth handoff", hash_="wip222222", author="Bob"),
            _commit(
                "fix flaky digest tests",
                hash_="fix333333",
                author="Bob",
                quality={"signal": "low", "reasons": ["generic fix-only subject"]},
            ),
        ]
    )

    output = build_team_digest_output(commit_data, template="github")

    assert output.startswith("# Team Workflow Digest\n")
    assert "_Template: GitHub_" in output
    assert "## Owner: Alice" in output
    assert "## Owner: Bob" in output
    assert "- Commits: 1 · Files: 1 · Lines: +5/-1" in output
    assert "PR: [#42 Add team digest](https://github.com/Tresnanda/git-standup/pull/42)" in output
    assert "Issue: [PROJ-7](https://jira.example/browse/PROJ-7)" in output
    assert "## Risk / Blocker Radar" in output
    assert "Bob: `wip22222` WIP auth handoff — keyword: wip" in output
    assert "Bob: `fix33333` fix flaky digest tests — low-signal, keyword: fix" in output
    assert "## Follow-up Questions" in output
    assert "Alice: Is PR #42 (Add team digest) ready for review, merge, or follow-up?" in output
    assert "Bob: Is `wip22222` still in progress or blocking handoff?" in output


def test_workflow_board_groups_prs_by_handoff_status() -> None:
    commit_data = cli._build_commit_data(
        [
            _commit(
                "feat: needs review",
                hash_="rev111111",
                pull_request={
                    "number": 10,
                    "title": "Needs review",
                    "url": "https://github.com/Tresnanda/git-standup/pull/10",
                    "state": "open",
                    "draft": False,
                    "checks": {
                        "state": "passed",
                        "total": 2,
                        "passed": 2,
                        "failed": 0,
                        "pending": 0,
                    },
                    "review_decision": "review_required",
                    "merge_state_status": "clean",
                    "updated_at": "2999-01-01T00:00:00Z",
                },
            ),
            _commit(
                "feat: ready to merge",
                hash_="mrg222222",
                pull_request={
                    "number": 11,
                    "title": "Ready to merge",
                    "url": "https://github.com/Tresnanda/git-standup/pull/11",
                    "state": "open",
                    "draft": False,
                    "checks": {
                        "state": "passed",
                        "total": 3,
                        "passed": 3,
                        "failed": 0,
                        "pending": 0,
                    },
                    "review_decision": "approved",
                    "merge_state_status": "clean",
                    "labels": ["release"],
                    "linked_issues": [
                        {
                            "number": 7,
                            "title": "Ship board",
                            "url": "https://github.com/Tresnanda/git-standup/issues/7",
                        }
                    ],
                    "updated_at": "2999-01-01T00:00:00Z",
                },
            ),
            _commit(
                "fix: owner action",
                hash_="act333333",
                author="Bob",
                pull_request={
                    "number": 12,
                    "title": "Fix owner action",
                    "state": "open",
                    "draft": True,
                    "checks": {
                        "state": "failed",
                        "total": 1,
                        "passed": 0,
                        "failed": 1,
                        "pending": 0,
                    },
                    "review_decision": "changes_requested",
                    "merge_state_status": "dirty",
                    "updated_at": "2020-01-01T00:00:00Z",
                },
            ),
        ]
    )

    output = build_workflow_board_output(commit_data, stale_days=7)

    assert output.startswith("# Workflow Status Board\n")
    assert "## Needs Review" in output
    assert "Alice: [#10 Needs review]" in output
    assert "review: review_required" in output
    assert "## Ready to Merge" in output
    assert "Alice: [#11 Ready to merge]" in output
    assert "Labels: release" in output
    assert (
        "Linked issues: [#7 Ship board](https://github.com/Tresnanda/git-standup/issues/7)"
        in output
    )
    assert "Merge owner to merge and communicate rollout notes." in output
    assert "## Owner Action" in output
    assert "Bob: #12 Fix owner action" in output
    assert "draft" in output
    assert "checks: failed (0 passed, 1 failed, 0 pending)" in output
    assert "Owner to confirm whether the draft is ready for review." in output


def test_team_digest_can_append_workflow_board() -> None:
    commit_data = cli._build_commit_data(
        [
            _commit(
                "feat: append board",
                hash_="brd444444",
                pull_request={
                    "number": 44,
                    "title": "Append board",
                    "state": "open",
                    "draft": False,
                    "review_decision": "approved",
                    "checks": {
                        "state": "passed",
                        "total": 1,
                        "passed": 1,
                        "failed": 0,
                        "pending": 0,
                    },
                    "merge_state_status": "clean",
                    "updated_at": "2999-01-01T00:00:00Z",
                },
            )
        ]
    )

    output = build_team_digest_output(commit_data, include_workflow_board=True)

    assert "# Team Workflow Digest" in output
    assert "## Workflow Status Board" in output
    assert "### Ready to Merge" in output


def test_cli_accepts_team_digest_template_contract() -> None:
    args = cli.parse_args(["--team-digest", "--template", "jira"])

    assert args.team_digest is True
    assert args.template == "jira"


def test_cli_workflow_board_implies_pr_status() -> None:
    args = cli.parse_args(["--workflow-board", "--stale-days", "3"])

    assert args.workflow_board is True
    assert args.include_prs is True
    assert args.pr_status is True
    assert args.stale_days == 3


def test_cli_rejects_unknown_team_digest_template() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--team-digest", "--template", "email"])


def test_team_digest_cli_uses_non_ai_formatter(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "get_commits",
        lambda **_: [
            _commit(
                "revert risky auth change",
                hash_="rev444444",
                pull_request={"number": 9, "title": "Rollback auth"},
            )
        ],
    )
    monkeypatch.setattr(
        cli,
        "generate_standup",
        lambda **_: (_ for _ in ()).throw(AssertionError("team digest should be non-AI")),
    )
    monkeypatch.setattr(
        cli,
        "generate_standup_with_harness",
        lambda **_: (_ for _ in ()).throw(AssertionError("team digest should be non-AI")),
    )

    exit_code = cli.main(["--team-digest", "--template", "linear", "--ai"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "# Team Workflow Digest" in captured.out
    assert "_Template: Linear_" in captured.out
    assert "revert risky auth change" in captured.out
    assert "Warning: --ai has no effect with --team-digest" in captured.err


def test_insights_formatter_surfaces_planning_themes_areas_risks_and_followups() -> None:
    commit_data = cli._build_commit_data(
        [
            _commit(
                "feat(auth): add login passkey rollout",
                hash_="fea111111",
                files=[
                    {"path": "src/auth/passkeys.py", "insertions": 80, "deletions": 4},
                    {"path": "tests/test_passkeys.py", "insertions": 30, "deletions": 0},
                ],
                pull_request={
                    "number": 42,
                    "title": "Add passkeys",
                    "url": "https://github.com/Tresnanda/git-standup/pull/42",
                },
            ),
            _commit(
                "fix login redirect rollback",
                hash_="fix222222",
                author="Bob",
                body="Rollback risky auth path before rollout.",
                files=[{"path": "src/auth/login.py", "insertions": 6, "deletions": 12}],
            ),
            _commit(
                "docs: update onboarding guide",
                hash_="doc333333",
                author="Casey",
                files=[{"path": "docs/onboarding.md", "insertions": 14, "deletions": 2}],
            ),
        ]
    )

    output = build_insights_output(commit_data)

    assert output.startswith("# Planning Insights\n")
    assert "## Themes" in output
    assert "Feature work: 1 commit(s)" in output
    assert "Fixes and stabilization: 1 commit(s)" in output
    assert "## Likely Product Areas" in output
    assert "Auth/Security" in output
    assert "Docs/Enablement" in output
    assert "## Review / Rollout Risks" in output
    assert "Bob: `fix22222` fix login redirect rollback" in output
    assert "keyword: revert" in output
    assert "## Suggested Follow-ups" in output
    assert "Confirm reviewer/merge plan for PR #42" in output
    assert "What validation confirms `fix22222` fixed the issue?" in output


def test_insights_formatter_handles_multiple_repositories_concisely() -> None:
    commit_data = {
        "_repositories": {
            "Tresnanda/api": cli._build_commit_data(
                [
                    _commit(
                        "feat(api): add usage endpoint",
                        hash_="api111111",
                        files=[{"path": "src/api/usage.py", "insertions": 20, "deletions": 1}],
                    )
                ]
            ),
            "Tresnanda/web": cli._build_commit_data(
                [
                    _commit(
                        "feat(ui): add usage dashboard",
                        hash_="web222222",
                        author="Bob",
                        files=[
                            {
                                "path": "src/components/UsageDashboard.tsx",
                                "insertions": 40,
                                "deletions": 3,
                            }
                        ],
                    )
                ]
            ),
        }
    }

    output = build_insights_output(commit_data)

    assert "Tresnanda/api" in output
    assert "Tresnanda/web" in output
    assert "API/Backend" in output
    assert "Frontend/UI" in output


def test_insights_cli_uses_non_ai_formatter(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "get_commits",
        lambda **_: [
            _commit(
                "WIP passkey rollout",
                hash_="wip444444",
                files=[{"path": "src/auth/passkeys.py", "insertions": 5, "deletions": 1}],
            )
        ],
    )
    monkeypatch.setattr(
        cli,
        "generate_standup",
        lambda **_: (_ for _ in ()).throw(AssertionError("insights should be non-AI")),
    )
    monkeypatch.setattr(
        cli,
        "generate_standup_with_harness",
        lambda **_: (_ for _ in ()).throw(AssertionError("insights should be non-AI")),
    )

    exit_code = cli.main(["--insights", "--ai"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "# Planning Insights" in captured.out
    assert "Auth/Security" in captured.out
    assert "WIP passkey rollout" in captured.out
    assert "Warning: --ai has no effect with --insights" in captured.err
