# Saved Report Profiles

## Goal

Let users save a repeatable `git-standup` command once and run it later without
remembering a long flag list. Keep v1 small and boring.

## Recommended v1 Command Shape

Use a `profile` command group with four actions:

```bash
git-standup profile run weekly
git-standup profile save weekly -- --remote-repo owner/api --days 7 --markdown --no-ai
git-standup profile list
git-standup profile delete weekly
```

Notes:

- `--` after `save <name>` separates profile metadata from the argv to store.
- `run` should replay the saved argv through normal argument parsing.
- `list` only prints profile names and a compact preview of the saved argv.
- `delete` removes one profile by name.

## Storage

Store profiles in the existing config file.

Reasoning:

- users already have one per-user config location;
- profiles are preferences, not repository state;
- keeping one file is simpler than inventing another path and migration story.

## Schema

Keep the saved shape to `profile name -> argv list`.

Example TOML shape:

```toml
[profiles]
weekly = ["--remote-repo", "owner/api", "--days", "7", "--markdown", "--no-ai"]
release = ["--since", "2026-01-01", "--until", "2026-01-07", "--changelog"]
```

Rules:

- profile names are unique keys;
- argv entries are plain strings in order;
- no secrets are stored;
- `--api-key` should be rejected at save time;
- environment-backed settings such as provider keys remain in env vars or the
  existing non-secret config fields.

## Config Parser Impact

The current parser only accepts top-level string fields and `[author_aliases]`.
Future implementation should extend it narrowly to accept one new `[profiles]`
section whose values are string lists. That is still compatible with the
project's small hand-rolled TOML subset; no TOML dependency is needed.

## Checkpoint Interaction

Profiles should not invent special checkpoint behavior.

- A saved argv list may include `--since-last`.
- A saved argv list may include `--write-checkpoint`.
- `profile run` should behave exactly like typing those flags directly.
- Checkpoints stay keyed by repository/remote target, not by profile name.

That keeps "saved profile" and "checkpoint state" separate and avoids a second
state model.

## Wizard Interaction

Do not change the wizard in v1.

Later, after the normal wizard builds deterministic argv, add one optional
prompt:

- "Save this command as a profile?"

If the user says yes, ask for a profile name and store the generated argv list.
The wizard should keep using `build_wizard_args`; profile saving should just
persist that output.

## Future Implementation Outline

1. Extend config parsing/formatting for `[profiles]`.
2. Add `profile run/save/list/delete` parsing in `cli.py`.
3. For `profile run`, load the saved argv and pass it back through `parse_args`.
4. Reject recursive profile execution in v1. A profile cannot save `profile run ...`.
5. Reject secret-bearing argv such as `--api-key`.

## Exact Tests Needed

Parser/config tests:

- parse `[profiles]` string-list entries;
- round-trip config with profiles plus existing AI defaults and author aliases;
- reject secret-bearing profile argv when saving;
- reject invalid profile names or malformed list values.

CLI tests:

- `parse_args(["profile", "run", "weekly"])` dispatches correctly;
- `profile save weekly -- ...` stores argv exactly in order;
- `profile list` prints saved names;
- `profile delete weekly` removes one entry;
- `profile run weekly` reuses existing behavior for `--remote-repo`,
  `--since-last`, `--write-checkpoint`, `--markdown`, `--no-ai`, and output files;
- invalid config still fails with the existing config error path.

Behavior tests:

- `profile run` preserves checkpoint semantics because it reuses normal parsing;
- multi-repo profiles keep per-repository checkpoint behavior;
- saved profiles do not print or persist secrets.

## Explicitly Out of Scope For v1

- profile editing in place;
- profile sharing or import/export;
- variables, templates, placeholders, or env interpolation;
- cron/scheduling features;
- repository-local profile files;
- nested profiles or profile inheritance;
- wizard-first profile management UI.

## Recommendation

Build v1 as argv persistence only. It matches how the CLI already works, keeps
the config model understandable, and lets every existing flag continue to own
its own semantics.
