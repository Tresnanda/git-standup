# Plan 001: Fix custom provider key environment setup

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in "STOP conditions" occurs, stop and report. When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat caff6db..HEAD -- src/git_standup/cli.py tests/test_cli.py`
> If either file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding. On mismatch, stop.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `caff6db`, 2026-06-24

## Why this matters

`git-standup config` supports a custom OpenAI-compatible provider and stores a
custom `api_key_env` in the config. The resolver already reads that configured
environment variable, but the interactive setup prompt still asks for the
provider default key name, which is `OPENAI_API_KEY` for `custom`. A user can
therefore configure `MY_GATEWAY_KEY`, paste the key, and end up with a saved
config that later looks for `MY_GATEWAY_KEY` while the prompt set
`OPENAI_API_KEY`.

## Current state

- `src/git_standup/cli.py` owns the interactive config flow.
- `tests/test_cli.py` already tests OpenAI setup but not the custom provider key env.
- `src/git_standup/ai_env.py` already resolves `config.api_key_env`; do not rewrite it unless tests prove it is wrong.

Current excerpt:

```python
# src/git_standup/cli.py:1574
if chosen == "custom" and not base_url:
    default_base_url = Prompt.ask("OpenAI-compatible base URL", default=default_base_url)
api_key_env = ""
if chosen == "custom":
    api_key_env = Prompt.ask("API key environment variable", default="OPENAI_API_KEY")
config = AIConfig(
    provider=chosen,
    base_url=base_url or default_base_url,
    model=model or (_prompt_model(default_model) if prompted else default_model),
    api_key_env=api_key_env,
    author_aliases=existing_author_aliases,
)
if allow_key:
    _prompt_api_key(chosen)
```

```python
# src/git_standup/cli.py:1440
def _prompt_api_key(provider: str) -> None:
    key_name = _provider_key_env(provider)
    key = getpass.getpass(f"{key_name} (hidden; leave blank to skip): ").strip()
```

```python
# src/git_standup/ai_env.py:302
return AIConnection(
    provider=provider or "custom",
    api_key=api_key_arg
    or (env.get(config.api_key_env, "") if config and config.api_key_env else "")
    or _api_key_for_provider(provider, env),
```

Repo conventions:

- Tests use pytest and monkeypatching; match `tests/test_cli.py:2623`.
- Keep helpers small and local to `cli.py`; avoid new modules.
- Existing style favors explicit small functions and no dependency additions.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `rtk .venv/bin/pytest tests/test_cli.py -k "configure_ai_interactive"` | all selected tests pass |
| Full tests | `rtk .venv/bin/pytest` | `225 passed` or higher |
| Lint | `rtk .venv/bin/ruff check .` | `All checks passed!` |

## Scope

**In scope**:

- `src/git_standup/cli.py`
- `tests/test_cli.py`
- `plans/README.md`

**Out of scope**:

- `src/git_standup/ai_env.py` unless the existing resolver test fails after the CLI fix.
- Installer scripts. They do not offer custom providers today.
- Adding a new config format or migration.

## Git workflow

- Branch: `advisor/001-fix-custom-provider-key-env`
- Commit message style: conventional commits, for example `fix: align custom provider key prompt`.
- Do not push unless instructed by the operator.

## Steps

### Step 1: Make `_prompt_api_key` accept an explicit env var name

Change `_prompt_api_key` in `src/git_standup/cli.py` so callers can pass an
optional env var name. Keep the current behavior as the default.

Target shape:

```python
def _prompt_api_key(provider: str, key_name: str | None = None) -> None:
    key_name = key_name or _provider_key_env(provider)
    ...
```

Do not change the persistence behavior, masking behavior, or prompt text except
for using the resolved `key_name`.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/cli.py` -> exits 0.

### Step 2: Pass the custom provider env name from interactive config

In `configure_ai_interactive`, when `allow_key` is true, call `_prompt_api_key`
with the configured custom key env:

```python
if allow_key:
    _prompt_api_key(chosen, api_key_env or None)
```

For built-in providers, `api_key_env` remains empty and behavior stays the same.

**Verify**: `rtk .venv/bin/ruff check src/git_standup/cli.py` -> exits 0.

### Step 3: Add regression coverage for custom provider setup

Add a test in `tests/test_cli.py` near
`test_configure_ai_interactive_sets_key_and_saves`. The test should:

- choose `provider`, then `custom`;
- answer a custom base URL such as `https://gateway.example/v1`;
- answer a custom env var such as `MY_GATEWAY_KEY`;
- return a fake secret from `getpass.getpass`;
- decline persistence;
- assert `os.environ["MY_GATEWAY_KEY"]` is set;
- assert `OPENAI_API_KEY` is not set by the flow;
- assert the saved config contains `provider = "custom"`, the custom base URL,
  and `api_key_env = "MY_GATEWAY_KEY"`;
- assert the secret value is not written to stdout, stderr, or config.

Use existing monkeypatch/capsys patterns from `test_configure_ai_interactive_sets_key_and_saves`.

**Verify**:
`rtk .venv/bin/pytest tests/test_cli.py -k "configure_ai_interactive"` -> all selected tests pass.

## Test plan

- New regression test in `tests/test_cli.py` for custom provider key env setup.
- Existing tests for built-in provider setup must still pass.
- Full verification:
  - `rtk .venv/bin/ruff check .`
  - `rtk .venv/bin/pytest`

## Done criteria

- [ ] Custom provider setup sets the user-selected key env, not `OPENAI_API_KEY`.
- [ ] Saved config still stores no secret values.
- [ ] Built-in provider setup behavior is unchanged.
- [ ] `rtk .venv/bin/ruff check .` passes.
- [ ] `rtk .venv/bin/pytest` passes.
- [ ] Only in-scope files are modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report if:

- `configure_ai_interactive` has already been refactored and the excerpt above no longer matches.
- Fixing this requires changing config parsing or `resolve_ai_connection`.
- Any test reveals a secret value printed or written to config.

## Maintenance notes

If custom provider setup is later added to installers, mirror this behavior
there too. Reviewers should check that the key value is never printed and never
written to `config.toml`.
