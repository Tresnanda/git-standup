# Plan 007: Design saved report profiles

> **Executor instructions**: This is a direction spike, not a build-everything
> implementation. Follow this plan step by step. Run every verification command
> and confirm the expected result before moving on. If anything in "STOP
> conditions" occurs, stop and report. When done, update the status row for this
> plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- src/git_standup/cli.py src/git_standup/config.py tests/test_cli.py README.md docs`
> If any in-scope file changed since this plan was written, compare the excerpts
> below against live code before proceeding.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `caff6db`, 2026-06-24

## Why this matters

The wizard already turns recurring choices into deterministic arguments, and
the README highlights recurring multi-repo, checkpoint, PR, and path-filtered
workflows. Users who run the same weekly report repeatedly currently have to
save shell aliases or remember a long command. Saved report profiles are a
natural product direction, but the config parser is intentionally small, so the
right first step is a tight design spike rather than a broad implementation.

## Current state

- `build_wizard_args` converts wizard answers into stable CLI args.
- Config currently stores AI defaults and author aliases only.
- README documents repeated workflows that would benefit from profiles.

Current excerpts:

```python
# src/git_standup/cli.py:595
def build_wizard_args(answers: dict[str, object]) -> list[str]:
    """Build deterministic git-standup arguments from wizard answers."""
```

```python
# src/git_standup/config.py:17
_ALLOWED_KEYS = {"provider", "base_url", "model", "harness", "api_key_env"}
```

```markdown
<!-- README.md:411 -->
git-standup --remote-repo Tresnanda/api --remote-repo Tresnanda/web --days 7 --markdown
```

Repo conventions:

- Keep config dependency-free; `config.py` parses a small TOML subset itself.
- Public CLI options are tested through `tests/test_cli.py`.
- Docs live in README; longer design notes can live under `docs/`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |
| Tests | `rtk .venv/bin/pytest` | `225 passed` or higher |

## Scope

**In scope for this spike**:

- `docs/report-profiles.md` (create)
- `README.md` (brief "planned design" or "future profile workflow" note only if useful)
- `plans/README.md`

**Optional in scope only if the design doc needs a tiny parser proof**:

- `src/git_standup/config.py`
- `tests/test_config.py`

**Out of scope**:

- Shipping `git-standup profile run`.
- Changing config schema in production code unless it is a minimal proof with tests.
- Wizard UI changes.
- New dependencies.

## Git workflow

- Branch: `advisor/007-design-report-profiles`
- Commit message style: `docs: design saved report profiles`
- Do not push unless instructed.

## Steps

### Step 1: Write the design doc

Create `docs/report-profiles.md`. It must answer these questions:

- What command shape should exist? Example candidates:
  - `git-standup profile run weekly`
  - `git-standup profile save weekly -- --remote-repo owner/api --days 7 --markdown --no-ai`
  - `git-standup profile list`
  - `git-standup profile delete weekly`
- Where are profiles stored? Prefer the existing config file unless the design
  shows why a separate file is simpler.
- What schema is stored? Keep it boring: profile name -> argv list. Do not store
  secrets.
- How does it interact with `--since-last` and `--write-checkpoint`?
- How does the wizard save a profile in a later implementation?
- What exact tests would a future implementation need?
- What is explicitly not included in v1?

Keep the recommended v1 small: run/list/save/delete is enough. Do not design
sharing, sync, templating, variables, or cron.

**Verify**: `rtk sed -n '1,220p' docs/report-profiles.md` -> the doc exists and answers the questions above.

### Step 2: Decide whether a parser proof is worth it

Read `src/git_standup/config.py`. If the design can be described clearly
without changing code, do not touch source. If a source proof is needed, limit it
to parsing/formatting profile argv lists in `config.py` plus tests in
`tests/test_config.py`.

Use this ceiling:

- no CLI command implementation;
- no migration;
- no wizard change;
- no dependency.

**Verify**:
If no source proof: `rtk .venv/bin/ruff check .` -> exits 0.
If source proof: `rtk .venv/bin/pytest tests/test_config.py` -> all pass.

### Step 3: Add a brief README pointer only if helpful

If `docs/report-profiles.md` is enough, skip README changes. If users browsing
README need to discover the design, add one short sentence under Development or
Limitations linking to the design doc. Do not imply the feature is already
implemented.

**Verify**: `rtk .venv/bin/ruff check .` -> exits 0.

## Test plan

- For doc-only spike: no tests required; run lint and full tests to ensure no
  accidental source impact.
- For optional parser proof:
  - add focused tests in `tests/test_config.py`;
  - run `rtk .venv/bin/pytest tests/test_config.py`;
  - run full suite.

## Done criteria

- [ ] `docs/report-profiles.md` exists and is self-contained.
- [ ] The doc recommends a minimal v1 command surface and storage schema.
- [ ] The doc names interactions with checkpoints, wizard, and secrets.
- [ ] No implementation is shipped unless limited to a tiny parser proof.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes if any source/test files changed.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- The design requires replacing the config parser with a TOML dependency.
- The feature cannot be scoped without templating or variable expansion.
- You find an existing profile implementation not noted in this plan.

## Maintenance notes

The best future implementation is likely small: store argv lists, reuse
`parse_args`, and let existing command behavior handle checkpoints and output.
Avoid inventing a separate report DSL unless real users hit argv-list limits.
