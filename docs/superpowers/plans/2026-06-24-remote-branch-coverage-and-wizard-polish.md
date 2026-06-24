# Remote Branch Coverage & Wizard Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `git-standup` report work on non-default branches, surface real clone errors, expose remote backend + branch coverage in the wizard, and restyle the wizard's interactive pickers ("Clean accent").

**Architecture:** Five focused changes, mostly in `src/git_standup/cli.py` plus `src/git_standup/gitlog.py` and one signature extension in `src/git_standup/github_api.py`. A new opt-in `--all-branches` flag drives `git log --all` in the clone/local backend; the wizard emits it (and `--remote-backend api`) from new prompts; the raw-mode pickers get an ANSI restyle that preserves the existing `rendered_lines` accounting.

**Tech Stack:** Python 3.10+, `rich` (already a dep), `pytest`, `argparse`, ANSI escape codes (already used in `cli.py`).

---

## Background (verified during investigation)

- Default `--remote-backend` is `clone` (cli.py:1849). Both backends read only the **default branch** — the API backend hits `/repos/{repo}/commits` with no `sha` (github_api.py:158), and the clone backend runs `git log` on HEAD. So "today's" feature/staging commits are invisible regardless of backend.
- A full `gh repo clone` already fetches every branch as `origin/*` refs, so `git log --all` in the clone backend surfaces non-default-branch commits.
- `_clone_remote_repo` (cli.py:1343) runs the clone with `capture_output=True` and raises a flat `"Could not clone remote repository {repo}"`, discarding git's real stderr / exit code / timeout reason.
- The run_wizard flow tests run in **non-TTY** mode → they exercise the `Prompt.ask` / `Confirm.ask` fallback in `_numbered_choice`/`_choice`/`_confirm`, **not** the raw-mode `_interactive_*` pickers. The picker restyle is tested separately via direct calls with `key_reader=`.
- No test asserts the old clone message or `validate_remote_api_options` (verified via grep) — both safe to change.

## File Structure

- Modify: `src/git_standup/cli.py` — `_clone_remote_repo` (error surfacing), argparse (`--all-branches`), `main` (wiring + warnings), `build_wizard_args` (emit flags), `run_wizard` (backend + branch prompts), `_style` helper, `_interactive_choice`, `_interactive_multi_select`, `_interactive_tabbed_multi_select`, `_collapse_summary`.
- Modify: `src/git_standup/gitlog.py` — `get_commits` gains `all_branches` param.
- Modify: `src/git_standup/github_api.py` — `validate_remote_api_options` rejects `--all-branches`.
- Test: `tests/test_gitlog.py`, `tests/test_cli.py`, `tests/test_github_api.py`.

Run all tests with: `.venv/bin/python -m pytest` (or `python -m pytest`).

---

### Task 1: Surface the real clone error

**Files:**
- Modify: `src/git_standup/cli.py:1343-1363` (`_clone_remote_repo`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (near `test_clone_remote_repo_does_not_use_blobless_filter`, ~line 2247). `types` and `subprocess` are already imported in that file.

```python
def test_clone_remote_repo_surfaces_git_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=128, cmd=cmd, output="", stderr="ERROR: SAML SSO enforced"
        )

    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        cli._clone_remote_repo("owner/name", tmp_path)

    message = str(excinfo.value)
    assert "owner/name" in message
    assert "SAML SSO enforced" in message


def test_clone_remote_repo_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        cli._clone_remote_repo("owner/name", tmp_path)

    assert "timed out" in str(excinfo.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_clone_remote_repo_surfaces_git_stderr tests/test_cli.py::test_clone_remote_repo_reports_timeout -v`
Expected: FAIL — current message is the generic `"Could not clone remote repository owner/name"` (no stderr / no "timed out").

- [ ] **Step 3: Split the except clause to surface the cause**

Replace the `try/except` in `_clone_remote_repo` (cli.py:1352-1363):

```python
    try:
        with _spinner(f"Cloning {_remote_repo_label(repo)}…"):
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Cloning {repo} timed out after 120s — check your network/VPN and retry"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"exit code {exc.returncode}"
        raise RuntimeError(f"Could not clone remote repository {repo}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not clone remote repository {repo}: {exc}") from exc
    return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k clone_remote_repo -v`
Expected: PASS (including the existing `test_clone_remote_repo_does_not_use_blobless_filter`).

- [ ] **Step 5: Commit**

```bash
git add src/git_standup/cli.py tests/test_cli.py
git commit -m "fix: surface real git error when remote clone fails"
```

---

### Task 2: Add `all_branches` to `get_commits`

**Files:**
- Modify: `src/git_standup/gitlog.py:130-238` (`get_commits`)
- Test: `tests/test_gitlog.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gitlog.py` (near `test_get_commits_appends_pathspecs_after_separator`, ~line 241). `subprocess` and `get_commits` are already imported.

```python
def test_get_commits_all_branches_adds_all_flag(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    get_commits(repo_path="/workspace/app", since="2026-01-01", all_branches=True)

    assert "--all" in calls[1]


def test_get_commits_without_all_branches_omits_all_flag(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    get_commits(repo_path="/workspace/app", since="2026-01-01")

    assert "--all" not in calls[1]


def test_get_commits_base_branch_takes_precedence_over_all_branches(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/workspace/app\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    get_commits(
        repo_path="/workspace/app",
        since="2026-01-01",
        base_branch="main",
        all_branches=True,
    )

    assert "--all" not in calls[1]
    assert "main..HEAD" in calls[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gitlog.py -k all_branches -v`
Expected: FAIL — `get_commits()` has no `all_branches` parameter (`TypeError: unexpected keyword argument`).

- [ ] **Step 3: Add the parameter and the `--all` flag**

In `gitlog.py`, add `all_branches: bool = False,` to the `get_commits` signature (after `exclude_merges: bool = False,`, before `pathspecs`):

```python
def get_commits(
    days: int = 7,
    author: str | None = None,
    base_branch: str | None = None,
    repo_path: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_commits: int | None = None,
    exclude_merges: bool = False,
    all_branches: bool = False,
    pathspecs: list[str] | None = None,
    author_aliases: AuthorAliases | None = None,
) -> list[dict[str, Any]]:
```

In the multi-author recursion block (the `for author_part in ...: get_commits(...)` call, ~line 160), pass `all_branches=all_branches,` through (add it alongside `exclude_merges=exclude_merges,`).

Then, in the command-construction section, after the `if exclude_merges:` block (cli currently ~line 211-212) and **before** `if base_branch:`, add:

```python
    if all_branches and not base_branch:
        cmd.append("--all")
```

(Placing it before `base_branch` keeps `base..HEAD` authoritative when both are set — matching the precedence test.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gitlog.py -v`
Expected: PASS (all gitlog tests, including the three new ones).

- [ ] **Step 5: Commit**

```bash
git add src/git_standup/gitlog.py tests/test_gitlog.py
git commit -m "feat: support --all-branches commit collection in get_commits"
```

---

### Task 3: Wire `--all-branches` into the CLI

**Files:**
- Modify: `src/git_standup/github_api.py:197-211` (`validate_remote_api_options`)
- Modify: `src/git_standup/cli.py` — argparse (~after line 1854), `main` (the two `get_commits` calls ~2270 and ~2316, the api-validate call ~2214, and a base-branch warning)
- Test: `tests/test_cli.py`, `tests/test_github_api.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_github_api.py` add:

```python
def test_validate_remote_api_options_rejects_all_branches() -> None:
    import pytest

    from git_standup.github_api import validate_remote_api_options

    with pytest.raises(RuntimeError) as excinfo:
        validate_remote_api_options(base_branch=None, pathspecs=None, all_branches=True)
    assert "--all-branches" in str(excinfo.value)
```

In `tests/test_cli.py` add:

```python
def test_parse_args_all_branches_defaults_false() -> None:
    args = cli.parse_args(["--remote-repo", "owner/name"])
    assert args.all_branches is False


def test_parse_args_accepts_all_branches() -> None:
    args = cli.parse_args(["--remote-repo", "owner/name", "--all-branches"])
    assert args.all_branches is True


def test_api_backend_rejects_all_branches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    exit_code = cli.main(
        [
            "--remote-repo",
            "owner/name",
            "--remote-backend",
            "api",
            "--all-branches",
            "--no-ai",
        ]
    )
    assert exit_code == 1
    assert "--all-branches" in capsys.readouterr().err


def test_clone_backend_passes_all_branches_to_get_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_commits(**kwargs):
        captured.update(kwargs)
        return _sample_commits()

    monkeypatch.setattr(cli, "_clone_remote_repo", lambda repo, parent: tmp_path)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", lambda: _TempDir(tmp_path))
    monkeypatch.setattr(cli, "get_commits", fake_get_commits)

    exit_code = cli.main(
        ["--remote-repo", "owner/name", "--all-branches", "--no-ai", "--markdown"]
    )

    assert exit_code == 0
    assert captured["all_branches"] is True
```

Note: `_sample_commits` and `_TempDir` are existing helpers in `tests/test_cli.py` (used by the remote-clone tests around lines 309-491). Reuse them as-is.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_github_api.py -k all_branches tests/test_cli.py -k "all_branches or api_backend_rejects" -v`
Expected: FAIL — `--all-branches` is not a recognized argument; `validate_remote_api_options` has no `all_branches` param.

- [ ] **Step 3a: Reject `--all-branches` in API mode**

In `github_api.py`, change `validate_remote_api_options`:

```python
def validate_remote_api_options(
    *, base_branch: str | None, pathspecs: list[str] | None, all_branches: bool = False
) -> None:
    """Raise a helpful error for git-native filters unsupported by API mode."""
    unsupported: list[str] = []
    if base_branch:
        unsupported.append("--base-branch")
    if pathspecs:
        unsupported.append("--path/--pathspec")
    if all_branches:
        unsupported.append("--all-branches")
    if unsupported:
        joined = " and ".join(unsupported)
        raise RuntimeError(
            f"--remote-backend api does not support {joined}. "
            "Use --remote-backend clone for git-native filtering."
        )
```

- [ ] **Step 3b: Add the argparse flag**

In `cli.py`, after the `--remote-backend` argument block (ends ~line 1854), add:

```python
    parser.add_argument(
        "--all-branches",
        action="store_true",
        help=(
            "Include commits from all branches, not just the default branch "
            "(clone backend / local repos only; not supported with --remote-backend api)"
        ),
    )
```

- [ ] **Step 3c: Wire it into `main`**

In `cli.py` `main`, update the API-validate call (~line 2214):

```python
                validate_remote_api_options(
                    base_branch=args.base_branch,
                    pathspecs=args.pathspecs,
                    all_branches=args.all_branches,
                )
```

Add `all_branches=args.all_branches,` to the **clone-backend** `get_commits(...)` call (~line 2270, the one with `repo_path=str(repo_path)`) and to the **local** `get_commits(...)` call (~line 2316, the one with `repo_path=args.repo`). Place it alongside `exclude_merges=args.exclude_merges,` in both.

Add a base-branch warning. Immediately after the `try:` that opens the commit-collection block (cli.py ~line 2202, right after `commit_fetch_limit = ...`), add:

```python
        if args.all_branches and args.base_branch:
            print(
                "Warning: --all-branches is ignored when --base-branch is set.",
                file=sys.stderr,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_github_api.py tests/test_cli.py -k "all_branches or api_backend_rejects or clone_backend_passes" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/git_standup/cli.py src/git_standup/github_api.py tests/test_cli.py tests/test_github_api.py
git commit -m "feat: add --all-branches flag for clone and local backends"
```

---

### Task 4: Emit new flags from `build_wizard_args`

**Files:**
- Modify: `src/git_standup/cli.py:595-657` (`build_wizard_args`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` near the other `test_build_wizard_args_*` tests (~line 1737):

```python
def test_build_wizard_args_emits_api_backend() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["owner/api"],
            "remote_backend": "api",
            "preset": "week",
            "format": "markdown",
            "ai": True,
        }
    )
    assert "--remote-backend" in args
    assert args[args.index("--remote-backend") + 1] == "api"


def test_build_wizard_args_clone_backend_omits_backend_flag() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["owner/api"],
            "remote_backend": "clone",
            "preset": "week",
            "format": "markdown",
            "ai": True,
        }
    )
    assert "--remote-backend" not in args


def test_build_wizard_args_emits_all_branches() -> None:
    args = cli.build_wizard_args(
        {
            "remote_repos": ["owner/api"],
            "remote_backend": "clone",
            "all_branches": True,
            "preset": "week",
            "format": "markdown",
            "ai": True,
        }
    )
    assert "--all-branches" in args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "build_wizard_args_emits or clone_backend_omits" -v`
Expected: FAIL — `build_wizard_args` never emits `--remote-backend` or `--all-branches`.

- [ ] **Step 3: Emit the flags**

In `build_wizard_args`, inside the `if isinstance(remote_repos, list) and remote_repos:` block (cli.py ~599-602), after the `for repo in remote_repos:` loop that appends `--remote-repo`, add:

```python
        if str(answers.get("remote_backend") or "clone") == "api":
            args.extend(["--remote-backend", "api"])
```

Then, just before the `preset = ...` line (~line 607), add:

```python
    if answers.get("all_branches"):
        args.append("--all-branches")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k build_wizard_args -v`
Expected: PASS (new tests plus all existing `build_wizard_args` tests).

- [ ] **Step 5: Commit**

```bash
git add src/git_standup/cli.py tests/test_cli.py
git commit -m "feat: emit --remote-backend and --all-branches from wizard args"
```

---

### Task 5: Ask backend + branch coverage in `run_wizard`

**Files:**
- Modify: `src/git_standup/cli.py:1625-1773` (`run_wizard`, the `repo_source == "remote"` branch ~1641-1642)
- Test: `tests/test_cli.py` (update `test_run_wizard_starts_with_repository_source_and_remote_multi_select`, ~1908)

Scope decision: the backend + all-branches prompts appear **only in the remote path** (where the default-branch limitation actually bites). Local-repo wizard runs are unchanged. The `--all-branches` CLI flag still works for local repos for power users.

- [ ] **Step 1: Update the existing remote wizard test (it will change behavior)**

Replace `test_run_wizard_starts_with_repository_source_and_remote_multi_select` (~line 1908) body so it answers the two new prompts and expects the new command. The new prompts in non-TTY mode are: backend via `_numbered_choice` (`Prompt.ask`, consumes one `answers` value) and all-branches via `_confirm` (`Confirm.ask`, consumes one `confirms` value, asked only for the clone backend).

```python
def test_run_wizard_starts_with_repository_source_and_remote_multi_select(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # repo source -> remote(3), repos -> 1,2, backend -> clone(1), preset -> week(2),
    # author -> all(1), format -> markdown(1)
    answers = iter(["3", "1,2", "1", "2", "1", "1"])
    # All branches? -> yes, Polish with AI? -> yes, Save? -> no, Run it now -> no
    confirms = iter([True, True, False, False])

    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli, "detect_ai_environment", lambda _env: dict(_AI_AVAILABLE))
    monkeypatch.setattr(
        cli,
        "_remote_repository_groups",
        lambda: {
            "Owned": ["Tresnanda/api", "Tresnanda/web"],
            "Organizations": ["Tresnanda/docs"],
            "Collaborator": [],
        },
    )

    assert cli.run_wizard() == 0

    out = capsys.readouterr().out
    assert "Repository source:" in out
    assert "Choose remote repositories:" in out
    assert "Remote backend:" in out
    assert (
        "Generated command:\n  git-standup --remote-repo Tresnanda/api "
        "--remote-repo Tresnanda/web --all-branches --days 7 --markdown"
    ) in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_run_wizard_starts_with_repository_source_and_remote_multi_select -v`
Expected: FAIL — no "Remote backend:" prompt exists yet; generated command lacks `--all-branches`.

- [ ] **Step 3: Add the prompts to `run_wizard`**

In `run_wizard`, replace the remote branch (cli.py:1641-1642):

```python
        elif repo_source == "remote":
            remote_repos = _choose_remote_repositories()
```

with:

```python
        elif repo_source == "remote":
            remote_repos = _choose_remote_repositories()
            remote_backend = _numbered_choice(
                "Remote backend",
                [
                    (
                        "clone",
                        "Clone",
                        "Full git history; supports all branches and path filters.",
                    ),
                    (
                        "api",
                        "GitHub API",
                        "Faster, no clone; default branch only.",
                    ),
                ],
                "clone",
            )
            answers_remote_backend = remote_backend
            answers_all_branches = False
            if remote_backend == "clone":
                answers_all_branches = _confirm(
                    "Include work on all branches, not just the default branch?",
                    default=True,
                )
```

Then, in the `answers: dict[str, object] = { ... }` construction a few lines below (cli.py ~1666-1671), after the `if remote_repos:` block that sets `answers["remote_repos"]`, add:

```python
        if remote_repos:
            answers["remote_repos"] = remote_repos
            answers["remote_backend"] = answers_remote_backend
            answers["all_branches"] = answers_all_branches
```

(Replace the existing two-line `if remote_repos: answers["remote_repos"] = remote_repos` with the block above. `answers_remote_backend` / `answers_all_branches` are only defined in the remote branch, so guard with `if remote_repos:` which is already the condition.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_run_wizard_starts_with_repository_source_and_remote_multi_select -v`
Expected: PASS.

- [ ] **Step 5: Run the full wizard test group to catch ripples**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "run_wizard or wizard" -v`
Expected: PASS. Local-path wizard tests (e.g. `test_run_wizard_asks_timeframe_then_author_then_format`) are unaffected because the new prompts only fire on the remote path. If any fail due to iterator drift, fix the affected test's `answers`/`confirms` iterators.

- [ ] **Step 6: Commit**

```bash
git add src/git_standup/cli.py tests/test_cli.py
git commit -m "feat: choose remote backend and branch coverage in wizard"
```

---

### Task 6: Restyle interactive pickers ("Clean accent")

**Files:**
- Modify: `src/git_standup/cli.py` — add `_style` helper; `_collapse_summary` (~890-893); `_interactive_choice.render` (~924-939); `_interactive_multi_select.render` (~1000-1017); `_interactive_tabbed_multi_select.render` (~1080-1105)
- Test: `tests/test_cli.py` (update 6 existing picker assertions)

Design: bold title (no colon), dim hint with `·` separators, cyan `❯` pointer on the cursor row (space otherwise), bold-cyan label on the selected single-choice row, dim descriptions/footers, green-check collapse `✓ {title} · {summary}`. **Header stays 2 lines** (title + hint) for `_interactive_choice`/`_interactive_multi_select` and 3 lines (title + tabs + hint) for the tabbed picker — so `rendered_lines` and `_picker_window` math are unchanged.

- [ ] **Step 1: Update the 6 existing picker assertions to the new styling**

In `tests/test_cli.py`:

1. `test_interactive_choice_uses_arrow_keys_to_select` (~2132-2134): replace
   ```python
       assert "Output format:" in out
       assert "Use Up/Down to move" in out
       assert "\x1b[4F\x1b[J" in out
   ```
   with
   ```python
       assert "\x1b[1mOutput format\x1b[0m" in out
       assert "↑/↓ move · ⏎ select · q quit" in out
       assert "\x1b[4F\x1b[J" in out
   ```

2. `test_interactive_choice_collapses_to_summary_on_confirm` (~2284): replace
   ```python
       assert "Output format: \x1b[32m✓\x1b[0m Plain text" in out
   ```
   with
   ```python
       assert "\x1b[32m✓\x1b[0m Output format · Plain text" in out
   ```

3. `test_multi_select_uses_arrow_keys_and_space_to_select` (~2050): replace
   ```python
       assert "Space selects" in out
   ```
   with
   ```python
       assert "space select" in out
   ```

4. `test_multi_select_scrolls_viewport_with_cursor` (~2089): replace
   ```python
       assert "> [ ] Tresnanda/repo-7" in out
   ```
   with
   ```python
       assert "\x1b[36m❯\x1b[0m [ ] Tresnanda/repo-7" in out
   ```

5. `test_tabbed_multi_select_uses_horizontal_arrows` (~2111-2112): replace
   ```python
       assert "Use Left/Right to switch tabs" in out
       assert "> [ ] org/web" in out
   ```
   with
   ```python
       assert "←/→ tabs · ↑/↓ move · space select · ⏎ confirm · a all · q quit" in out
       assert "\x1b[36m❯\x1b[0m [ ] org/web" in out
   ```

6. `test_multi_select_collapses_to_summary_on_confirm` (~2299) and `test_multi_select_empty_selection_summary_says_none` (~2311): replace
   ```python
       assert "Choose authors: \x1b[32m✓\x1b[0m Kevin, YusufRehan" in out
   ```
   with
   ```python
       assert "\x1b[32m✓\x1b[0m Choose authors · Kevin, YusufRehan" in out
   ```
   and
   ```python
       assert "Choose authors: \x1b[32m✓\x1b[0m none" in capsys.readouterr().out
   ```
   with
   ```python
       assert "\x1b[32m✓\x1b[0m Choose authors · none" in capsys.readouterr().out
   ```

- [ ] **Step 2: Run the updated tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "interactive_choice or multi_select or tabbed" -v`
Expected: FAIL — current renderers still print the old hint/pointer/colon/collapse text.

- [ ] **Step 3: Add the `_style` helper**

In `cli.py`, near the other small helpers (e.g. just above `_move_cursor_up`, ~line 864), add:

```python
def _style(text: str, code: str) -> str:
    """Wrap text in an ANSI SGR code, resetting afterward."""
    return f"\x1b[{code}m{text}\x1b[0m"
```

- [ ] **Step 4: Restyle `_collapse_summary`**

Replace (cli.py:890-893):

```python
def _collapse_summary(title: str, summary: str, rendered_lines: int) -> None:
    """Erase a finished picker frame and leave a one-line summary behind."""
    _move_cursor_up(rendered_lines)
    print(f"{_style('✓', '32')} {title} · {summary}")
```

- [ ] **Step 5: Restyle `_interactive_choice.render`**

Replace the `render()` body (cli.py:924-939) with:

```python
    def render() -> None:
        nonlocal rendered_lines
        _move_cursor_up(rendered_lines)
        start, end, paged = _picker_window(cursor, len(options))
        print(_style(title, "1"))
        print(_style("↑/↓ move · ⏎ select · q quit", "2"))
        for index in range(start, end):
            _value, label, description = options[index]
            if index == cursor:
                row = f"{_style('❯', '36')} {_style(label, '1;36')}"
            else:
                row = f"  {label}"
            if description:
                row += f"  {_style('· ' + description, '2')}"
            print(row)
        footer_lines = 0
        if paged:
            print(_style(f"Showing {start + 1}-{end} of {len(options)}.", "2"))
            footer_lines = 1
        rendered_lines = (end - start) + 2 + footer_lines
```

- [ ] **Step 6: Restyle `_interactive_multi_select.render`**

Replace the `render()` body (cli.py:1000-1017) with:

```python
    def render() -> None:
        nonlocal rendered_lines
        _move_cursor_up(rendered_lines)
        start, end, paged = _picker_window(cursor, len(display))
        print(_style(title, "1"))
        print(_style("↑/↓ move · space select · ⏎ confirm · a all · q quit", "2"))
        for index in range(start, end):
            pointer = _style("❯", "36") if index == cursor else " "
            if index == add_index:
                print(f"{pointer} {display[index]}")
            else:
                mark = "[x]" if index in selected else "[ ]"
                print(f"{pointer} {mark} {display[index]}")
        footer_lines = 0
        if paged:
            print(
                _style(
                    f"Showing {start + 1}-{end} of {len(display)}. "
                    f"Selected: {len(selected)}.",
                    "2",
                )
            )
            footer_lines = 1
        rendered_lines = (end - start) + 2 + footer_lines
```

- [ ] **Step 7: Restyle `_interactive_tabbed_multi_select.render`**

Replace the `render()` body (cli.py:1080-1105) with:

```python
    def render() -> None:
        nonlocal rendered_lines
        _move_cursor_up(rendered_lines)
        tab_name, options = current_tab()
        cursor = cursor_by_tab[tab_name]
        start, end, paged = _picker_window(cursor, len(options))
        print(_style(title, "1"))
        rendered_tabs = " | ".join(
            f"[{name}]" if index == tab_index else name
            for index, (name, _values) in enumerate(tabs)
        )
        print(f"Tabs: {rendered_tabs}")
        print(
            _style(
                "←/→ tabs · ↑/↓ move · space select · ⏎ confirm · a all · q quit",
                "2",
            )
        )
        for index in range(start, end):
            repo = options[index]
            pointer = _style("❯", "36") if index == cursor else " "
            mark = "[x]" if repo in selected else "[ ]"
            print(f"{pointer} {mark} {repo}")
        footer_lines = 0
        if paged:
            print(
                _style(
                    f"Showing {start + 1}-{end} of {len(options)}. "
                    f"Selected: {len(selected)}.",
                    "2",
                )
            )
            footer_lines = 1
        rendered_lines = (end - start) + 3 + footer_lines
```

- [ ] **Step 8: Run the picker tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "interactive_choice or multi_select or tabbed or confirm" -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/git_standup/cli.py tests/test_cli.py
git commit -m "feat: restyle wizard pickers with clean accent styling"
```

---

### Task 7: Full suite, lint, and docs

**Files:**
- Modify: `README.md` (document `--all-branches`), `CHANGELOG.md` (if present and maintained)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS (entire suite).

- [ ] **Step 2: Lint**

Run: `.venv/bin/python -m ruff check src tests`
Expected: no errors. Fix any line-length (100) or import-order issues introduced.

- [ ] **Step 3: Document `--all-branches`**

In `README.md`, add `--all-branches` to the options/flags reference next to `--remote-backend`, e.g.:

> `--all-branches` — Include commits from every branch, not just the default. Works with local repos and `--remote-backend clone`; not supported with `--remote-backend api`.

Add an example line near the other `--remote-repo` examples:

> `git-standup --remote-repo owner/api --all-branches --author me`

Add a one-line entry to `CHANGELOG.md` if the project maintains one.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document --all-branches flag"
```

---

## Self-Review

- **Spec coverage:** (1) clone error surfacing → Task 1. (2) `--all-branches` "today" fix → Tasks 2-3. (3) wizard backend + branch prompts → Tasks 4-5. (4) Clean-accent TUI restyle → Task 6. Full-suite/lint/docs → Task 7. All four agreed changes are covered.
- **Placeholder scan:** No TBD/TODO; every code and test step shows complete code and exact commands.
- **Type/name consistency:** `all_branches` (snake_case param) ↔ `--all-branches` (CLI) ↔ `answers["all_branches"]` (wizard) ↔ `args.all_branches` (argparse dest) are consistent. `_style(text, code)` signature is used identically everywhere. `validate_remote_api_options(..., all_branches=...)` matches its new callsite.
- **Ambiguity:** `--all-branches` + `--base-branch` precedence is explicit (base wins; warning printed). Wizard branch prompt is remote-clone-only by explicit scope decision. Header line counts are explicitly held at 2 (choice/multi) and 3 (tabbed) so redraw math is unchanged.
