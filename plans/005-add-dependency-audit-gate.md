# Plan 005: Add a dependency vulnerability audit gate

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in "STOP conditions" occurs, stop and report. When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- pyproject.toml .github/workflows/ci.yml CONTRIBUTING.md .github/PULL_REQUEST_TEMPLATE.md`
> If any in-scope file changed since this plan was written, compare the excerpts
> below against live code before proceeding.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `caff6db`, 2026-06-24

## Why this matters

CI currently runs lint, tests, build, and wheel smoke tests, but does not check
for vulnerable Python dependencies. The project uses network-capable packages
such as `httpx` and installer/dev dependencies, so a cheap audit gate is useful.
During the advisor audit, `python -m pip_audit` failed because `pip-audit` is not
installed.

## Current state

Current excerpts:

```toml
# pyproject.toml:31
[project.optional-dependencies]
dev = [
    "build>=1.2",
    "pytest>=8.0",
    "ruff>=0.4.0",
    "twine>=5.0",
]
```

```yaml
# .github/workflows/ci.yml:44
- name: Lint
  run: ruff check .

- name: Test
  run: pytest

- name: Build package
  run: python -m build
```

```markdown
<!-- CONTRIBUTING.md:14 -->
ruff check .
pytest
python -m build
python -m twine check dist/*
```

Repo conventions:

- Dev tooling is listed under `[project.optional-dependencies].dev`.
- CI installs `".[dev]"` once before running tools.
- PR template lists expected local verification commands.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Install dev tools | `rtk .venv/bin/python -m pip install -e ".[dev]"` | exit 0 |
| Audit | `rtk .venv/bin/python -m pip_audit` | exits 0, no vulnerabilities |
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |
| Tests | `rtk .venv/bin/pytest` | `225 passed` or higher |

## Scope

**In scope**:

- `pyproject.toml`
- `.github/workflows/ci.yml`
- `CONTRIBUTING.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `plans/README.md`

**Out of scope**:

- Source code changes.
- Dependency upgrades to fix vulnerabilities discovered by the audit. If found,
  stop and report.
- Adding a lockfile.

## Git workflow

- Branch: `advisor/005-add-dependency-audit-gate`
- Commit message style: `ci: audit python dependencies`
- Do not push unless instructed.

## Steps

### Step 1: Add `pip-audit` to dev dependencies

Add `pip-audit>=2.7` to `[project.optional-dependencies].dev` in
`pyproject.toml`. Keep alphabetical order if you choose to sort the small list,
but do not reformat unrelated metadata.

**Verify**:
`rtk .venv/bin/python -m pip install -e ".[dev]"` -> exits 0 and installs `pip-audit`.

### Step 2: Add a CI audit step

In `.github/workflows/ci.yml`, add a step after lint or after tests:

```yaml
- name: Audit dependencies
  run: python -m pip_audit
```

Do not make it advisory-only. If vulnerabilities are present, CI should fail.

**Verify**:
`rtk .venv/bin/python -m pip_audit` -> exits 0 with no vulnerability findings.

### Step 3: Update local contributor commands

Update `CONTRIBUTING.md` and `.github/PULL_REQUEST_TEMPLATE.md` so contributors
see the new audit command alongside lint/tests/build. Keep the docs short.

**Verify**: `rtk .venv/bin/ruff check .` -> exits 0.

## Test plan

- No Python tests are needed for CI YAML/docs dependency wiring.
- Run:
  - `rtk .venv/bin/python -m pip install -e ".[dev]"`
  - `rtk .venv/bin/python -m pip_audit`
  - `rtk .venv/bin/ruff check .`
  - `rtk .venv/bin/pytest`

## Done criteria

- [ ] `pip-audit` is available via `".[dev]"`.
- [ ] CI fails on dependency vulnerabilities.
- [ ] Contributor docs and PR checklist include the audit command.
- [ ] `rtk .venv/bin/python -m pip_audit` exits 0.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- `pip-audit` reports an existing vulnerability. Do not suppress it in the same
  change; create a separate remediation plan.
- Installing `".[dev]"` upgrades unrelated tooling in a way that breaks tests.
- CI syntax needs a workflow restructuring beyond adding one step.

## Maintenance notes

If the project later adopts a lockfile, consider auditing the lockfile rather
than the installed environment. For now, the installed environment matches the
current CI setup and is the smallest useful gate.
