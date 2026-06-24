# Plan 002: Bind checkpoint writes to the fetched report window

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in "STOP conditions" occurs, stop and report. When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- src/git_standup/cli.py tests/test_cli.py README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `caff6db`, 2026-06-24

## Why this matters

`--write-checkpoint` records a timestamp before fetching, but the report fetch
does not use that timestamp as `--until`. Commits created during a slower report
can be included while the saved checkpoint remains earlier than those commits,
so the next `--since-last` report can repeat them. Explicit `--until` has the
opposite risk: the current code saves "now" even when the user reported only
through an earlier window, which can skip later commits.

## Current state

- `src/git_standup/cli.py` owns checkpoint timestamp creation and commit fetch calls.
- `tests/test_cli.py` has since-last tests but does not assert the fetch `until`.
- `README.md` describes checkpoint semantics.

Current excerpts:

```python
# src/git_standup/cli.py:2240
checkpoint_targets: list[_CheckpointTarget] = []
checkpoint_timestamp = _checkpoint_timestamp() if args.write_checkpoint else ""
since_by_checkpoint_id: dict[str, str] = {}
```

```python
# src/git_standup/cli.py:2252
def _finish_success() -> int:
    if not args.write_checkpoint:
        return 0
    try:
        _write_report_checkpoints(checkpoint_targets, checkpoint_timestamp)
```

```python
# src/git_standup/cli.py:2390
commits = get_commits(
    days=args.days,
    author=args.author,
    ...
    since=repo_since,
    until=args.until,
```

```markdown
<!-- README.md:270 -->
`--write-checkpoint` stores the current timestamp after a successful report, so the next run can pick up from there.
```

Repo conventions:

- CLI tests monkeypatch fetch functions and inspect kwargs. Match
  `tests/test_cli.py:1045`.
- Dates use `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS +0000`; do not introduce a new
  timestamp format.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `rtk .venv/bin/pytest tests/test_cli.py -k "checkpoint or since_last"` | all selected tests pass |
| Full tests | `rtk .venv/bin/pytest` | `225 passed` or higher |
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |

## Scope

**In scope**:

- `src/git_standup/cli.py`
- `tests/test_cli.py`
- `README.md`
- `plans/README.md`

**Out of scope**:

- `src/git_standup/checkpoint.py` storage format.
- Git log parsing.
- Remote API date parser unless an existing test fails.

## Git workflow

- Branch: `advisor/002-bind-checkpoint-to-fetch-window`
- Commit message style: `fix: bind checkpoints to fetched report windows`
- Do not push unless instructed.

## Steps

### Step 1: Introduce one effective checkpoint window end

In `main`, compute a single window-end value when `args.write_checkpoint` is
true:

- if `args.until` is set, use `args.until`;
- otherwise use `_checkpoint_timestamp()`.

Use that same value for:

- the fetch `until` argument when no explicit `args.until` was supplied;
- the saved checkpoint timestamp on success.

Keep behavior unchanged when `--write-checkpoint` is absent.

One acceptable shape:

```python
checkpoint_timestamp = ""
checkpoint_until = args.until
if args.write_checkpoint:
    checkpoint_timestamp = args.until or _checkpoint_timestamp()
    checkpoint_until = checkpoint_timestamp
```

Then pass `until=checkpoint_until` to local, clone, and API fetches. If you pick
a different helper name, keep it simple and local to `main`.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/cli.py` -> exits 0.

### Step 2: Add tests for implicit and explicit checkpoint windows

Update or add tests in `tests/test_cli.py`:

1. Existing `test_since_last_uses_checkpoint_and_can_write_next_checkpoint`
   should assert `captured["until"] == "2026-06-08 17:30:00 +0000"`.
2. Existing `test_write_checkpoint_updates_after_success_with_no_commits`
   should assert the fetch received `until` equal to the generated checkpoint
   timestamp.
3. Add a test for `--until 2026-06-07 --write-checkpoint --no-ai` proving:
   - the fetch receives `until == "2026-06-07"`;
   - the checkpoint stores `"2026-06-07"`, not "now".

Use monkeypatches rather than creating real repositories.

**Verify**:
`rtk .venv/bin/pytest tests/test_cli.py -k "checkpoint or since_last"` -> all selected tests pass.

### Step 3: Update README wording

Adjust the checkpoint section to say that `--write-checkpoint` stores the end of
the successful fetched window. For normal runs, that end is the timestamp taken
before fetching; for explicit `--until`, it is the supplied `--until`.

Do not expand this into a long tutorial.

**Verify**: `rtk .venv/bin/ruff check .` -> exits 0.

## Test plan

- Focused checkpoint CLI tests in `tests/test_cli.py`.
- Full suite after the behavior change.
- No real checkpoint files outside pytest temp dirs.

## Done criteria

- [ ] `--write-checkpoint` without `--until` fetches through the same timestamp it saves.
- [ ] `--write-checkpoint --until X` saves `X`, not wall-clock now.
- [ ] Local, remote clone, and remote API fetch paths all use the same effective `until`.
- [ ] README describes the new semantics.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- A fetch backend rejects the generated checkpoint timestamp format.
- The change requires modifying checkpoint JSON schema.
- Existing tests prove a documented workflow depends on saving wall-clock now
  while fetching an earlier explicit `--until`.

## Maintenance notes

Reviewers should scrutinize all three fetch paths. Future checkpoint features
should treat "saved checkpoint" and "report window end" as the same concept.
