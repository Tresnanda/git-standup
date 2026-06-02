# Decouple output format from AI, add clipboard copy

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Problem

The wizard's "Output style" menu crams two unrelated decisions into one list of
four options (`cli.py:651-668`):

```
1) AI summary  - Use your configured AI provider or CLI for a polished draft.
2) Markdown    - Paste-ready Markdown for Slack, Notion, GitHub, or a file.
3) Plain text  - Simple terminal summary without AI.
4) JSON        - Structured data for scripts, dashboards, or automation.
```

This conflates two orthogonal axes:

- **Format** — Markdown / Plain text / JSON
- **AI polish** — on / off

The proof of the conflation is in the option text itself: "AI summary" and
"Plain text … without AI" are the *same content* (a summary) differing only by
the AI toggle. As a result, AI-polished Markdown — an obviously useful output —
is **impossible** today: `--markdown` and the AI path are mutually exclusive
early-returns in `main()` (`cli.py:920-935`), and the formatters in
`formatter.py` are pure templates with no AI involvement.

One combination is *not* meaningful: **AI + JSON**. JSON exists for
deterministic automation; running it through an LLM defeats its purpose. So the
two axes are orthogonal except for that one excluded cell.

## Goal

Split the single menu into two clean questions (format, then AI), unlock
AI-polished Markdown, and never present an option that can't produce a real
result. Separately, when output is printed (not saved to a file), offer to copy
it to the clipboard with a single keypress.

## The output matrix

|              | Markdown            | Plain text             | JSON          |
|--------------|---------------------|------------------------|---------------|
| **Raw**      | template            | template               | template      |
| **AI**       | NEW                 | today's "AI summary"   | excluded      |

Five meaningful outputs. The design produces exactly these and no nonsense
combinations.

## Wizard flow (approach B — "smart split")

Replace the single `_numbered_choice("Output style", …)` with:

1. **Format** (`_numbered_choice`, default Markdown):
   ```
   Output format:
     1) Markdown    - paste-ready for Slack, Notion, GitHub
     2) Plain text  - simple terminal summary
     3) JSON        - structured data for scripts/automation
   ```
   Stored as `answers["format"]` ∈ `{markdown, text, json}`.

2. **AI polish** — skipped entirely when `format == json` (`answers["ai"] =
   False`). Otherwise depends on whether a provider is available:

   - **Provider available** → simple toggle:
     ```
     Polish with AI? [Y/n] (y)
     ```
   - **No provider available** → offer on-the-fly setup instead of silently
     hiding AI:
     ```
     Polish with AI? No AI provider detected.
       1) Set one up now
       2) Skip AI (raw output)
     ```
     "Set one up now" runs `configure_ai_interactive()` (see below). On success
     `answers["ai"] = True`; if the user cancels setup, fall back to
     `answers["ai"] = False`.

   Stored as `answers["ai"]` (bool). "Provider available" reuses the detection
   already computed in the wizard (`ai_report` from `detect_ai_environment`, plus
   saved config): true if any env API key, any detected CLI harness, or a saved
   provider/harness exists.

3. **Save to file?** (unchanged `Confirm.ask`, default no). When saving,
   `_default_output_path` is unchanged (markdown→`.md`, json→`.json`, else
   `.txt`); AI-markdown still saves as `.md` because format is `markdown`
   regardless of the AI toggle.

The conflated `"ai"` format value is removed entirely; `format` is now strictly
a format, `ai` is a separate boolean.

## CLI flag model

AI becomes the default for narrative formats; `--no-ai` is the opt-out. Add an
explicit `--ai` flag for symmetry and conflict detection.

| Invocation                | Output                         |
|---------------------------|--------------------------------|
| `git-standup`             | AI plain text (unchanged)      |
| `--no-ai`                 | raw plain text (unchanged)     |
| `--markdown`              | **AI markdown** (CHANGED)      |
| `--markdown --no-ai`      | raw markdown template          |
| `--json`                  | raw JSON (always; AI ignored)  |
| `--json --ai`             | raw JSON + stderr warning      |

**Behavior change:** bare `--markdown` previously produced a raw template; it now
produces AI markdown. This is intentional and matches the wizard default. Update
the `--markdown` help string and the epilog examples (`cli.py:701-704`)
accordingly, and add an example for `--markdown --no-ai`.

### `build_wizard_args` mapping (`cli.py:418-424`)

```
format == "json"                  -> ["--json"]
format == "markdown",  ai         -> ["--markdown"]
format == "markdown",  not ai     -> ["--markdown", "--no-ai"]
format == "text",      ai         -> []            # AI text is the default
format == "text",      not ai     -> ["--no-ai"]
```

### `main()` dispatch (`cli.py:920-979`)

Restructure the early-return ladder:

```
if args.json:
    if args.ai: warn "--ai ignored with --json" to stderr
    emit build_json_output(...)            # always raw
    return 0

ai_enabled = not args.no_ai
fmt = "markdown" if args.markdown else "text"

if not ai_enabled:
    emit build_markdown_output(...) if fmt == "markdown" else text output
    return 0

# AI mode, format-aware
standup_text = generate_standup[_with_harness](..., output_format=fmt)
# on RuntimeError: fall back to the raw formatter matching fmt
emit standup_text
return 0
```

The AI fallback-on-failure path should fall back to the formatter matching the
chosen format (markdown→`build_markdown_output`, text→`build_text_output`),
rather than always text.

### AI prompt (`ai.py`)

`_build_prompt`, `generate_standup`, and `generate_standup_with_harness` gain an
`output_format` parameter (`"text"` | `"markdown"`, default `"text"`):

- `markdown`: instruct the model to use Markdown (headings, bullet lists, bold)
  — paste-ready for Slack/Notion/GitHub.
- `text`: instruct plain prose / simple bullets with no Markdown syntax.

## On-the-fly AI provider setup

Today the `git-standup config` command (`run_config_command`, `cli.py:544-608`)
configures a provider interactively but does **not** handle the API key, and the
installer (`install.sh`) handles the key but writes TOML directly. The wizard
needs both. Extract one shared helper and reuse it in all three places.

### `configure_ai_interactive(config_path, *, allow_key=True) -> AIConfig | None`

New function in `cli.py` (or a small `ai_setup.py`), called by both
`run_config_command` and the wizard. Steps:

1. **Choose a provider** from a numbered list: the `PROVIDER_SPECS` providers,
   the `codex` CLI harness, local harnesses (`ollama`, `lms`), and `custom`.
   Returning/cancelling yields `None`.
2. **Fill defaults** from the chosen `ProviderSpec` (or `_HARNESS_DEFAULTS`):
   `base_url`, `model` — both editable via `Prompt.ask` with the spec value as
   default. `custom` prompts for base URL + model with no defaults.
3. **API key** (only for HTTP API providers, and only when `allow_key`): prompt
   masked via `getpass.getpass()`. The env var name is the provider spec's first
   `key_names` entry (e.g. `OPENAI_API_KEY`); `custom` uses `OPENAI_API_KEY`.
   - Set it in `os.environ` immediately so AI works **this run** (the wizard
     calls `main()` in-process at `cli.py:677`, so the key is visible).
   - **Offer to persist** (`Confirm.ask`, default no): append `export VAR="…"`
     to the shell profile on Unix / `setx VAR "…"` on Windows (see
     `env_persist.py` below). codex / ollama / lms harnesses need no key — skip
     this step.
4. **Save** non-secret defaults via `save_config` (`provider`/`base_url`/`model`,
   or `harness` for CLI harnesses). Secrets are never written — `config.py`
   already enforces this.
5. Return the resulting `AIConfig`.

`run_config_command`'s `set-provider`/`set-cli` paths are refactored to delegate
to this helper (passing `allow_key=True`), removing duplicated prompting.

### `env_persist.py` (new, no dependency)

- `persist_env_var(name, value) -> Path | str | None` — appends `export
  name="value"` to the user's shell rc on Unix (zsh → `~/.zshrc`, bash →
  `~/.bashrc`, else `~/.profile`, chosen from `$SHELL`), de-duping an existing
  line for the same var; on Windows runs `setx name "value"` via `subprocess`.
  Returns the path/target touched, or `None` on failure. Mirrors `install.sh`'s
  profile-writing behavior.
- The key is masked in all log output; only the `export`/`setx` line is written.

### Caveat to surface to the user

If the user declines to persist the key, the wizard's printed "Generated
command" will still work *this* run (env is set in-process) but not in a fresh
shell. The wizard notes this when the key is not persisted.

## Clipboard copy

New module `clipboard.py`. No third-party dependency (respects the project's
dependency-safety rules — `pyperclip` is avoidable).

- `copy_to_clipboard(text: str) -> bool` — shells out via `subprocess` to the
  first available OS tool, returns success:
  - macOS: `pbcopy`
  - Windows: `clip`
  - Linux: first of `wl-copy`, `xclip -selection clipboard`,
    `xsel --clipboard --input` found via `shutil.which`
  - none found → return `False`
- `_read_single_key() -> str` — single keypress without Enter via
  `termios`/`tty` (Unix) or `msvcrt` (Windows); falls back to line `input()`
  when raw mode is unavailable (e.g. non-TTY).

### Integration point

Centralize printed-output emission in one helper so every printed branch behaves
identically:

```
def _emit_output(content, output_path, *, printer=None) -> None:
    if _write_output(content, output_path):
        return                      # saved to file → no copy prompt
    if printer: printer()           # rich render (text / AI branches)
    else: print(content, end="...")
    _maybe_offer_copy(content)
```

```
def _maybe_offer_copy(content) -> None:
    if not sys.stdout.isatty():
        return                      # piped/redirected → no prompt
    print copy hint; key = _read_single_key()
    if key.lower() == "c":
        ok = copy_to_clipboard(content)
        print "Copied to clipboard ✓"  or  "No clipboard tool found"
```

- The copy prompt appears **only** when output was printed (not saved) **and**
  stdout is a TTY.
- The clipboard receives the underlying plain content string (e.g.
  `build_text_output(...)` / `standup_text` / JSON / markdown), not Rich markup.
- All five printed branches route through `_emit_output`.

## Components & boundaries

- `clipboard.py` (new) — OS clipboard + single-key read. No app knowledge.
- `env_persist.py` (new) — persist an env var to shell profile / `setx`. No app
  knowledge.
- `configure_ai_interactive()` (new, in `cli.py` or `ai_setup.py`) — shared
  provider-setup flow used by the wizard and `run_config_command`.
- `cli.py` — `build_wizard_args` mapping, wizard prompts (incl. on-the-fly AI
  step), `main()` dispatch, `_emit_output` / `_maybe_offer_copy`, new `--ai`
  flag, updated help/epilog, `run_config_command` refactored onto the shared
  helper.
- `ai.py` — `output_format` parameter threaded through prompt + both generators.
- `ai_env.py` / `config.py` — unchanged (reused: `PROVIDER_SPECS`,
  `_HARNESS_DEFAULTS`, `save_config`).
- `formatter.py` — unchanged.

## Testing

Existing suite: `tests/test_cli.py` (24K), plus `test_ai.py`, etc.

New / updated tests:

- `build_wizard_args`: all five format×ai combinations map to the right flags.
- `main()` dispatch: `--markdown` → AI path (mock generator), `--markdown
  --no-ai` → `build_markdown_output`, `--json --ai` → JSON + warning, `--no-ai`
  unchanged.
- AI prompt: `output_format="markdown"` vs `"text"` produce distinguishable
  instructions; default stays `"text"`.
- `clipboard.copy_to_clipboard`: success and "no tool" paths with `subprocess` /
  `shutil.which` mocked; platform branch selection.
- `_maybe_offer_copy`: prompts only when `isatty()` true and no output path; `c`
  triggers copy, other keys / non-TTY skip.
- **Update** any existing test asserting `--markdown` == raw template.
- `configure_ai_interactive`: provider selection fills spec defaults; key prompt
  sets `os.environ` and is offered for persistence; codex/ollama/lms skip the key
  prompt; cancel returns `None`; `save_config` called with non-secret fields only.
- `env_persist.persist_env_var`: appends/de-dupes on Unix profile (tmp `$HOME`);
  Windows `setx` branch with `subprocess` mocked.
- Wizard AI step: no-provider path offers set-up-now vs skip; success sets
  `answers["ai"] = True`, cancel sets it `False`.
- `run_config_command` still saves the right config after delegating to the
  shared helper (existing config tests stay green).

## Out of scope

- AI-generated JSON (deliberately excluded).
- Persisting the AI-toggle preference to config.
- Clipboard for content saved to a file.
- On-the-fly provider setup when a provider is *already* available (the wizard
  only offers setup when none is detected; use `git-standup config` to change an
  existing one).
- Storing API keys in `config.toml` (forbidden by design; keys go to env /
  shell profile only).
