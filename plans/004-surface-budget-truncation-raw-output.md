# Plan 004: Surface output budget truncation in raw reports

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in "STOP conditions" occurs, stop and report. When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- src/git_standup/cli.py src/git_standup/formatter.py tests/test_cli.py tests/test_changelog.py`
> If any in-scope file changed since this plan was written, compare the excerpts
> below against live code before proceeding.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `caff6db`, 2026-06-24

## Why this matters

`--max-commits` and `--max-files-per-commit` intentionally limit output and AI
input. JSON exposes metadata and changelog mode prints a truncation note, but
raw text, Markdown, and stats output can silently look complete while omitting
commits or files. Users paste these reports into status updates, so the report
should say when it is budgeted.

## Current state

- `src/git_standup/cli.py` creates `budget_metadata` in `_apply_output_budget`.
- `build_changelog_output` already accepts budget metadata and formats a note.
- `build_markdown_output`, `build_text_output`, and `build_stats_output` do not
  accept or display budget metadata.

Current excerpts:

```python
# src/git_standup/cli.py:183
metadata = {
    "truncated": commits_truncated or files_truncated,
    "limits": {
        "max_commits": max_commits,
        "max_files_per_commit": max_files_per_commit,
    },
```

```python
# src/git_standup/formatter.py:1178
def _format_changelog_budget_note(budget_metadata: dict[str, Any]) -> str:
    ...
    return "_Note: output was truncated — " + "; ".join(notes) + "._"
```

```python
# src/git_standup/cli.py:2511
def _emit_raw() -> None:
    if output_format == "markdown":
        markdown = build_markdown_output(commit_data)
```

Repo conventions:

- Formatter functions return strings and stay dependency-free.
- CLI tests monkeypatch `get_commits` and inspect output with `capsys`.
- Changelog tests already assert the truncation note pattern.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `rtk .venv/bin/pytest tests/test_cli.py -k "max_commits or max_files or stats"` | all selected tests pass |
| Full tests | `rtk .venv/bin/pytest` | `225 passed` or higher |
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |

## Scope

**In scope**:

- `src/git_standup/formatter.py`
- `src/git_standup/cli.py`
- `tests/test_cli.py`
- `tests/test_changelog.py` only if shared note wording changes
- `plans/README.md`

**Out of scope**:

- AI prompt budgeting internals in `src/git_standup/ai.py`.
- JSON metadata shape.
- Changing default budget values.

## Git workflow

- Branch: `advisor/004-surface-budget-truncation-raw-output`
- Commit message style: `fix: note budgeted raw standup output`
- Do not push unless instructed.

## Steps

### Step 1: Generalize the budget note formatter

In `formatter.py`, either rename `_format_changelog_budget_note` to a generic
private helper or add a second small helper for text output. It must summarize:

- commit list limited to N when `commits_truncated` is true;
- file lists limited to N when `files_truncated` is true;
- files omitted count when present.

Keep the changelog output wording stable unless tests are updated deliberately.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/formatter.py` -> exits 0.

### Step 2: Thread budget metadata into raw formatters

Update these formatter signatures with an optional `budget_metadata` parameter:

- `build_markdown_output(commit_data, budget_metadata=None)`;
- `build_text_output(commit_data, budget_metadata=None)`;
- `build_stats_output(commit_data, output_format="text", budget_metadata=None)`.

Place the note near the top of each output, after the title/scope line and
before detailed sections. Markdown can use the existing italic note style; text
should use plain `Note: output was truncated - ...`.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/formatter.py` -> exits 0.

### Step 3: Pass metadata from CLI raw paths

In `src/git_standup/cli.py`:

- pass `budget_metadata` to `build_markdown_output` and `build_text_output` in
  `_emit_raw`;
- pass `budget_metadata` to `build_stats_output`.

Do not change JSON or AI behavior.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/cli.py` -> exits 0.

### Step 4: Add tests

Add tests in `tests/test_cli.py` covering:

- `--markdown --no-ai --max-commits 1` prints a truncation note;
- `--no-ai --max-files-per-commit 1` prints a truncation note;
- `--stats-only --max-commits 1` prints a truncation note.

Use existing sample commit helpers and capsys patterns. Keep assertions on
wording specific enough to catch omission but not overly brittle.

**Verify**:
`rtk .venv/bin/pytest tests/test_cli.py -k "max_commits or max_files or stats"` -> all selected tests pass.

## Test plan

- New CLI output tests for Markdown, text, and stats truncation notes.
- Existing changelog truncation test must keep passing.
- Full verification:
  - `rtk .venv/bin/ruff check .`
  - `rtk .venv/bin/pytest`

## Done criteria

- [ ] Raw text, raw Markdown, and stats reports visibly mention budget truncation.
- [ ] Changelog still mentions truncation.
- [ ] JSON metadata remains unchanged.
- [ ] AI prompt budgeting remains unchanged.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- Adding the note requires changing commit_data shape.
- Tests reveal downstream assumptions that formatter signatures cannot change.
- The note cannot be added without duplicating large formatter branches.

## Maintenance notes

Any future output mode that consumes budgeted commit data should either include
the note or expose equivalent metadata. Reviewers should check both CLI paths:
normal single-repo output and multi-repo output.
