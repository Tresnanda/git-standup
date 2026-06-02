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

2. **AI polish** — shown **only when** `format != json` **AND** an AI provider
   is available:
   ```
   Polish with AI? [Y/n] (y)
   ```
   Stored as `answers["ai"]` (bool). When `format == json`, or no provider is
   available, the question is skipped and `answers["ai"] = False`.

   "AI provider available" reuses the existing detection already computed in the
   wizard (`ai_report` from `detect_ai_environment`, plus saved config): true if
   any env API key, any detected CLI harness, or a saved provider/harness exists.

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
- `cli.py` — `build_wizard_args` mapping, wizard prompts, `main()` dispatch,
  `_emit_output` / `_maybe_offer_copy`, new `--ai` flag, updated help/epilog.
- `ai.py` — `output_format` parameter threaded through prompt + both generators.
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

## Out of scope

- AI-generated JSON (deliberately excluded).
- Persisting the AI-toggle preference to config.
- Clipboard for content saved to a file.
