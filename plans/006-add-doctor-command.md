# Plan 006: Add a `doctor` command for environment diagnostics

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in "STOP conditions" occurs, stop and report. When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- src/git_standup/cli.py tests/test_cli.py README.md skills/git-standup/SKILL.md`
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

The installer and wizard already detect Python, Git, AI keys, CLI harnesses,
saved config, and GitHub CLI availability. Users troubleshooting "why is AI not
working?" or "why can this repo not be summarized?" currently have to infer the
state from several flows. A read-only `git-standup doctor` command can reuse the
existing checks and produce a safe diagnostic report without prompting for keys
or making paid API calls.

## Current state

- `parse_args` already dispatches commands such as `wizard`, `update`, and
  `config`.
- `detect_ai_environment` reports masked keys and harnesses.
- `get_repo_root` checks whether a path is a Git repo.
- `gh` is already optional for PR/remote workflows.

Current excerpts:

```python
# src/git_standup/cli.py:2142
if target == "wizard":
    args.command = "wizard"
...
elif target == "update":
    args.command = "update"
...
elif target == "config":
    args.command = "config"
```

```python
# src/git_standup/ai_env.py:199
def detect_ai_environment(env: Mapping[str, str], which=default_which, config=None) -> dict[str, object]:
    """Detect AI keys and local CLI harnesses without making paid API calls."""
```

```python
# src/git_standup/gitlog.py:104
def get_repo_root(repo_path: str | None = None) -> str:
```

Repo conventions:

- CLI tests live in `tests/test_cli.py` and call `cli.main([...])`.
- User-facing command examples belong in README and the bundled skill when
  agents should know about the command.
- Preserve no-AI local workflows.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `rtk .venv/bin/pytest tests/test_cli.py -k "doctor"` | all selected tests pass |
| Full tests | `rtk .venv/bin/pytest` | `225 passed` or higher |
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |

## Scope

**In scope**:

- `src/git_standup/cli.py`
- `tests/test_cli.py`
- `README.md`
- `skills/git-standup/SKILL.md`
- `plans/README.md`

**Out of scope**:

- Network calls to providers.
- Persisting config or modifying shell profiles.
- A plugin architecture or interactive repair flow.

## Git workflow

- Branch: `advisor/006-add-doctor-command`
- Commit message style: `feat: add environment doctor command`
- Do not push unless instructed.

## Steps

### Step 1: Add command parsing for `doctor`

Update `parse_args` so `git-standup doctor` sets `args.command = "doctor"` and
rejects extra positional arguments. Add it to the help epilog.

**Verify**:
`rtk .venv/bin/pytest tests/test_cli.py -k "parse_args"` -> existing parse tests pass.

### Step 2: Implement a read-only `run_doctor_command`

Add a small function in `cli.py`, near `run_config_command`, that prints a
plain text diagnostic report. Keep it read-only. Suggested sections:

- `git-standup`: installed version and config path;
- `Python`: current `sys.version_info` and whether it meets `MIN_PYTHON`;
- `Git`: whether `shutil.which("git")` exists and whether `get_repo_root(args.repo)`
  succeeds for the current directory;
- `GitHub CLI`: whether `gh` is on PATH;
- `AI`: masked API key providers, supported harnesses, unsupported credentials,
  saved config provider/harness/model; never print secret values;
- `Next steps`: one-line hints when no Git repo or no AI provider is available.

Exit code:

- return `0` when the command ran and printed diagnostics, even if warnings are
  present;
- return `1` only when config parsing raises `ValueError`, because that blocks
  normal AI resolution too.

**Verify**:
`rtk .venv/bin/ruff check src/git_standup/cli.py` -> exits 0.

### Step 3: Wire `main`

In `main`, dispatch `args.command == "doctor"` before commit fetching, like
`config` and `update`.

**Verify**:
`rtk .venv/bin/pytest tests/test_cli.py -k "doctor"` -> selected tests pass once added.

### Step 4: Add tests

Add tests in `tests/test_cli.py` for:

- `parse_args(["doctor"])` sets command to `doctor`;
- `cli.main(["doctor"])` prints version/config/Git/AI sections and returns 0;
- secrets are masked: set `OPENAI_API_KEY` and assert the raw value is absent;
- invalid config returns 1 and prints a helpful error.

Use monkeypatches for `config_path`, `load_config`, `shutil.which`, and
`get_repo_root` as needed.

**Verify**:
`rtk .venv/bin/pytest tests/test_cli.py -k "doctor"` -> all doctor tests pass.

### Step 5: Document the command

Add a short README example:

```bash
git-standup doctor
```

Mention that it is read-only and masks credentials. Update
`skills/git-standup/SKILL.md` so agents can use `doctor` when setup is unclear.

**Verify**: `rtk .venv/bin/ruff check .` -> exits 0.

## Test plan

- New CLI tests in `tests/test_cli.py`.
- Full verification:
  - `rtk .venv/bin/ruff check .`
  - `rtk .venv/bin/pytest`

## Done criteria

- [ ] `git-standup doctor` exists and is read-only.
- [ ] It never prints raw API keys.
- [ ] It reports Git repo status, GitHub CLI status, AI config/detection, and config path.
- [ ] It exits 0 for normal warnings and 1 for invalid config.
- [ ] README and skill mention the command.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- Implementing `doctor` requires network calls.
- The command needs to write config, checkpoints, or shell profiles.
- Adding diagnostics requires a large new module. Keep it in `cli.py` for now.

## Maintenance notes

Future diagnostics can grow, but keep the command safe by default: no paid API
calls, no secret printing, no mutation. Reviewers should read the full doctor
output in tests and check secret masking.
