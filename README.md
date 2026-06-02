# git-standup

Generate standup-ready summaries from Git history.

`git-standup` reads recent commits from the current repository, groups the work by author and date, and prints either a local text summary or an AI-generated narrative. It is designed for solo developers, maintainers, and small teams who want a fast way to turn commit history into a useful weekly update.

## Highlights

- Summarizes commits from the last N days.
- Groups work by author and date.
- Shows file-level change stats in local text mode.
- Exports structured JSON for automation and reporting.
- Exports paste-ready Markdown for GitHub, Slack, Notion, or weekly notes.
- Builds non-AI changelog Markdown for release notes with `--changelog`.
- Can run against another repository path with `--repo`.
- Can focus reports on one or more paths with repeatable `--path` filters.
- Supports exact report windows with `--since` and `--until`.
- Can hide merge commits with `--exclude-merges` for less noisy standups.
- Writes summaries directly to files with `--output`.
- Supports AI summaries through OpenAI-compatible chat-completion APIs.
- Works without AI by using `--no-ai`.
- Can compare the current branch against a base branch.

## Installation

Requires Python 3.10 or newer and Git.

macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/Tresnanda/git-standup/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/Tresnanda/git-standup/main/install.ps1 | iex
```

For unattended installs, pass `--yes`:

```bash
curl -fsSL https://raw.githubusercontent.com/Tresnanda/git-standup/main/install.sh | bash -s -- --yes
```

The installer uses `pipx`, checks Git, and offers a simple numbered AI setup. You can choose Codex CLI, OpenAI, Gemini, OpenRouter, or skip AI setup. If you paste an API key during install, it is saved to your user shell environment; the app config stores only provider/model defaults. It may ask whether to star the GitHub repo, defaulting to yes; if it cannot star from the terminal, it prints the repo link instead. After install, run `git-standup` in your terminal to start the guided report builder. The wizard checks for a newer GitHub version and asks before updating.

Manual install:

```bash
pipx install .
```

For local development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

Run from inside any Git repository:

```bash
git-standup week
```

On an interactive terminal, the bare command opens a guided command builder with numbered choices for the report type and output style, then shows the generated command before running it:

```bash
git-standup
git-standup wizard
```

Example wizard choices:

```text
Report type:
  1) This week - Last 7 days for the whole repo.
  2) My commits - Only commits authored by me.
  3) Branch changes - Compare this branch against a base branch.
  4) Custom range - Choose days, dates, or author filters.

Output format:
  1) Markdown - Paste-ready for Slack, Notion, or GitHub.
  2) Plain text - Simple terminal summary.
  3) JSON - Structured data for scripts or automation.
  4) Changelog - Release-note Markdown grouped by conventional commit type.

Polish with AI? [Y/n]
```

The wizard asks for a format, then whether to polish it with AI (skipped for
JSON and changelog). Markdown and Plain text can each be produced with or
without AI. If no AI provider is detected, the wizard offers to set one up on
the fly. When the report is printed instead of saved, press `c` to copy it to
the clipboard.

Update to the latest GitHub version at any time:

```bash
git-standup update
```

To force the previous immediate default report from an interactive shell, use:

```bash
git-standup --no-wizard
```

Summarize the last day:

```bash
git-standup --days 1 --no-ai
```

Summarize only your commits:

```bash
git-standup me
```

Export structured JSON:

```bash
git-standup --json
```

Print AI-polished Markdown (or add `--no-ai` for a raw template):

```bash
git-standup --markdown
```

Generate release-note style changelog Markdown without AI:

```bash
git-standup --since 2026-01-01 --until 2026-01-07 --changelog
```

Run from anywhere against another repository:

```bash
git-standup ../api --markdown
```

Focus a report on commits that touched specific paths:

```bash
git-standup --path src --path tests --no-ai
```

Path filters are Git pathspecs passed to `git log` after `--`, so they work with
`--repo`, date filters, author filters, and branch comparisons.

Generate an exact reporting window:

```bash
git-standup --since 2026-01-01 --until 2026-01-07 --markdown
```

Hide merge commits for a cleaner activity summary:

```bash
git-standup --exclude-merges --markdown
```

Write output directly to a file:

```bash
git-standup --markdown --output standup.md
```

Use the shorter output alias:

```bash
git-standup me --out standup.txt
```

## AI Summaries

By default, `git-standup` tries to generate a natural-language summary with an OpenAI-compatible API. Set an API key with either an environment variable or a CLI flag:

```bash
export OPENAI_API_KEY="sk-..."
git-standup
```

`git-standup` can also auto-detect OpenAI-compatible keys such as `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `PERPLEXITY_API_KEY`, and `XAI_API_KEY`. Claude/Anthropic keys are intentionally not detected or promoted by the installer.

Save a default provider and model:

```bash
git-standup config
git-standup config set-provider --provider gemini --model gemini-3.5-flash
git-standup config show
```

Defaults live at `$XDG_CONFIG_HOME/git-standup/config.toml` or `~/.config/git-standup/config.toml` on macOS/Linux, and `%APPDATA%\git-standup\config.toml` on Windows. The file stores only `provider`, `base_url`, `model`, and optional `harness`; API keys stay in environment variables or `--api-key`.

Resolution priority is:

1. CLI flags such as `--api-key`, `--provider`, `--base-url`, and `--model`.
2. Saved defaults from `git-standup config`.
3. Detected environment variables.
4. Built-in OpenAI-compatible defaults.

Reset saved defaults:

```bash
git-standup config reset
```

Use the installed Codex CLI instead of an API key:

```bash
git-standup config set-cli --harness codex
git-standup config set-cli --harness codex --model gpt-5
```

When Codex is selected, `git-standup` runs `codex exec` in read-only mode and uses your existing Codex login/config. No API key is required by `git-standup`.

Use a custom model:

```bash
git-standup --model gpt-4o-mini
```

Use an OpenAI-compatible endpoint such as Ollama, LocalAI, Azure OpenAI, or an internal gateway:

```bash
git-standup \
  --base-url http://localhost:11434/v1 \
  --model llama3.1
```

If AI generation fails, the command falls back to the local text summary and exits with a non-zero status so CI or scripts can detect the degraded path.

## Common Workflows

### Weekly Personal Update

```bash
git-standup --author me --days 7
```

### Branch Review Summary

Show commits on the current branch that are not in `main`:

```bash
git-standup branch
```

### Automation-Friendly JSON

```bash
git-standup --days 14 --json --output standup.json
```

The JSON output is grouped as `author -> date -> commits/stats`, making it easy to feed into dashboards, release notes, or another summarization step.

### Paste-Ready Markdown

```bash
git-standup --author me --days 7 --markdown --output standup.md
```

Markdown mode preserves the local, no-AI workflow while producing output that is easy to paste into issue comments, pull request updates, team notes, or status docs.

### Release Notes Changelog

```bash
git-standup --since 2026-01-01 --until 2026-01-07 --changelog --output changelog.md
```

Changelog mode is always non-AI. It groups conventional commits into Features, Fixes, Docs, Refactors, Chores, and Other, strips conventional prefixes for cleaner bullets, and includes changed-file/line stats plus top touched files for quick release-note context. It respects the same filters and ranges as other modes, including `--author`, `--base-branch`, `--max-commits`, and `--max-files-per-commit`.

## CLI Reference

```text
usage: git-standup [-h] [--days DAYS] [--repo REPO]
                   [--since SINCE] [--until UNTIL] [--author AUTHOR]
                   [--base-branch BASE_BRANCH] [--max-commits MAX_COMMITS]
                   [--max-files-per-commit MAX_FILES_PER_COMMIT]
                   [--exclude-merges] [--path PATH] [--json] [--no-ai]
                   [--ai] [--markdown] [--changelog] [--output OUTPUT]
                   [--api-key API_KEY] [--provider PROVIDER]
                   [--harness HARNESS] [--model MODEL]
                   [--base-url BASE_URL] [--version] [--no-wizard]
                   [command|preset|repo ...]

options:
  command|preset|repo  Optional command or preset: wizard, config, me, week,
                       branch, update; or a repository path.
  --days DAYS          Number of days of Git history to include. Must be positive.
  --repo PATH          Path to the Git repository to analyze.
  --since DATE         Start date for the report window, in YYYY-MM-DD format.
  --until DATE         End date for the report window, in YYYY-MM-DD format.
  --author AUTHOR      Filter by author. Use "me" for the current Git user.
  --base-branch NAME   Show commits in HEAD that are not in this base branch.
  --max-commits N      Maximum commits to include in output and AI input.
  --max-files-per-commit N
                       Maximum changed files to include per commit.
  --exclude-merges     Exclude merge commits from Git history.
  --path, --pathspec PATH
                        Only include commits touching this pathspec. Repeat for
                        multiple paths.
  --json               Print structured JSON (always raw; AI is ignored).
  --no-ai              Skip AI and print a raw formatted summary.
  --ai                 Force AI mode (default for text/markdown; ignored with --json).
  --markdown           Print a Markdown summary (AI-polished unless --no-ai).
  --changelog          Print release-note Markdown grouped by conventional commit
                       category (always raw; AI is ignored).
  --output, --out PATH Write JSON, Markdown, text, or AI output to a file.
  --api-key KEY        API key for AI summaries. Defaults to OPENAI_API_KEY.
  --provider NAME      Provider override or config provider name.
  --harness NAME       CLI harness for config set-cli, such as codex, ollama,
                       or lms.
  --model NAME         Chat model name. Defaults to gpt-4o-mini.
  --base-url URL       OpenAI-compatible API base URL.
  --version            Print the installed version.
  --no-wizard          Run the default report instead of the interactive guide.
```

## Example Text Output

```text
Weekly Standup Summary

Alice
  Tue Mar 10, 2026
    [abc123] Add authentication middleware
      src/auth.py (+120 -8)
      tests/test_auth.py (+48)

Summary
  Total Commits: 1
  Total Files Changed: 2
  Total Lines Added: 168
  Total Lines Removed: 8
```

## Limitations

- Commit quality affects summary quality. Squashed or vague commits produce less useful output.
- Binary file changes are retained and counted with zero line changes because Git reports them without numeric stats.
- AI summaries send commit metadata and file paths to the configured API provider.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT. See [LICENSE](LICENSE).
