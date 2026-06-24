# Plan 003: Escape git and GitHub controlled Markdown in derived reports

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in "STOP conditions" occurs, stop and report. When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- src/git_standup/formatter.py tests/test_formatter_escape.py tests/test_team_digest.py`
> If any in-scope file changed since this plan was written, compare the excerpts
> below against live code before proceeding.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `caff6db`, 2026-06-24

## Why this matters

The original Markdown and Rich terminal paths have escaping tests, but newer
derived Markdown reports use several raw git/GitHub-controlled strings. A commit
subject, PR title, label, issue title, repo name, or URL with Markdown syntax can
mislead readers when pasted into GitHub, Slack, or Notion. This is not remote
code execution, but it is a trust-boundary output bug for a CLI that summarizes
untrusted repository metadata.

## Current state

- `src/git_standup/formatter.py` has `_escape_markdown_text` and `_markdown_code_span`.
- `tests/test_formatter_escape.py` covers `build_markdown_output`,
  `build_changelog_output`, and Rich terminal output.
- `build_insights_output` and workflow board formatting still use raw text in
  several Markdown locations.

Current excerpts:

```python
# src/git_standup/formatter.py:772
lines.append(
    f"- {item['author']}: `{_commit_hash_short(commit)}` "
    f"{commit.get('subject', 'Untitled commit')}{repo_note} — "
    f"{_risk_reason_text(risk['reasons'])}"
)
```

```python
# src/git_standup/formatter.py:580
label = f"#{number} {title}"
linked = f"[{label}]({url})" if url else label
status_bits = _workflow_status_bits(item)
lines = [f"- {owner}: {linked}{repo_note} · {' · '.join(status_bits)}"]
```

```python
# src/git_standup/formatter.py:632
label = f"#{number}" if number is not None else title or url
if title and number is not None:
    label = f"{label} {title}"
parts.append(f"[{label}]({url})" if url else label)
```

Repo conventions:

- Existing Markdown escaping helpers live in `formatter.py`; reuse them.
- Existing security regression tests live in `tests/test_formatter_escape.py`.
- Do not add a Markdown library.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Escape tests | `rtk .venv/bin/pytest tests/test_formatter_escape.py tests/test_team_digest.py` | all pass |
| Full tests | `rtk .venv/bin/pytest` | `225 passed` or higher |
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |

## Scope

**In scope**:

- `src/git_standup/formatter.py`
- `tests/test_formatter_escape.py`
- `tests/test_team_digest.py` only if an existing assertion needs updating
- `plans/README.md`

**Out of scope**:

- Plain text output. It is not Markdown.
- AI prompt redaction. That is covered in `tests/test_ai.py`.
- Changing the report structure or headings.

## Git workflow

- Branch: `advisor/003-escape-derived-markdown-surfaces`
- Commit message style: `fix: escape derived markdown report text`
- Do not push unless instructed.

## Steps

### Step 1: Add a tiny Markdown link helper if needed

If raw URLs need to be emitted as links, add one small helper in
`formatter.py`. Keep it boring:

- escape the link label with existing `_escape_markdown_text`;
- encode or strip characters that break Markdown link targets, at minimum
  whitespace, `<`, `>`, and `)`;
- return plain escaped label when URL is empty.

Do not add a dependency.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/formatter.py` -> exits 0.

### Step 2: Escape workflow board Markdown

Update `_format_workflow_board_item`, `_format_linked_issues`, and related
workflow helpers so these values are escaped before interpolation:

- owner names;
- repo names;
- PR titles;
- labels;
- linked issue titles;
- evidence commit subjects.

Keep code spans for hashes. Keep URLs clickable when safe.

**Verify**:
`rtk .venv/bin/pytest tests/test_team_digest.py -k "workflow_board"` -> all selected tests pass.

### Step 3: Escape insights Markdown

Update `build_insights_output`, `_insights_evidence`, `_format_insights_bucket`,
and `_format_inline_paths` so these values are escaped or code-spanned:

- author;
- commit subject;
- repo name;
- file paths;
- follow-up text that embeds user-controlled titles.

Use `_markdown_code_span` for file paths instead of raw backticks.

**Verify**:
`rtk .venv/bin/pytest tests/test_team_digest.py -k "insights"` -> all selected tests pass.

### Step 4: Add regression tests

Extend `tests/test_formatter_escape.py` with malicious data that exercises:

- `build_insights_output`;
- `build_workflow_board_output`;
- raw PR title and issue title containing `[link](https://evil.test)` and
  backticks;
- repo or author containing Markdown delimiters.

Assertions should prove literal text is escaped and no attacker-controlled link
label is rendered unescaped.

**Verify**:
`rtk .venv/bin/pytest tests/test_formatter_escape.py tests/test_team_digest.py` -> all pass.

## Test plan

- Add escaping tests in `tests/test_formatter_escape.py`.
- Keep existing team digest and workflow board tests passing.
- Full verification:
  - `rtk .venv/bin/ruff check .`
  - `rtk .venv/bin/pytest`

## Done criteria

- [ ] Derived Markdown reports escape author/repo/subject/title/label text.
- [ ] Existing report shape is preserved.
- [ ] New tests fail on the old behavior and pass on the fix.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- Escaping changes require rewriting the entire formatter module.
- A desired link target cannot be made safe without changing output semantics.
- Existing tests prove consumers depend on raw Markdown in these derived reports.

## Maintenance notes

Any future paste-ready Markdown report should use the same helpers. Reviewers
should search for new `f"[...](...)"` and raw backtick interpolation in
`formatter.py`.
