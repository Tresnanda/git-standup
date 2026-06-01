# git-standup

Generate standup-ready summaries from Git history.

`git-standup` reads recent commits from the current repository, groups the work by author and date, and prints either a local text summary or an AI-generated narrative. It is designed for solo developers, maintainers, and small teams who want a fast way to turn commit history into a useful weekly update.

## Highlights

- Summarizes commits from the last N days.
- Groups work by author and date.
- Shows file-level change stats in local text mode.
- Exports structured JSON for automation and reporting.
- Exports paste-ready Markdown for GitHub, Slack, Notion, or weekly notes.
- Can run against another repository path with `--repo`.
- Supports exact report windows with `--since` and `--until`.
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

On an interactive terminal, the bare command opens a guided command builder that asks for the repository, report preset, output format, and optional output file, then shows the generated command before running it:

```bash
git-standup
git-standup wizard
```

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

Print Markdown:

```bash
git-standup --markdown
```

Run from anywhere against another repository:

```bash
git-standup ../api --markdown
```

Generate an exact reporting window:

```bash
git-standup --since 2026-01-01 --until 2026-01-07 --markdown
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

## CLI Reference

```text
usage: git-standup [-h] [--days DAYS] [--repo REPO]
                   [--since SINCE] [--until UNTIL] [--author AUTHOR]
                   [--base-branch BASE_BRANCH] [--json] [--no-ai]
                   [--markdown] [--output OUTPUT]
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
  --json               Print structured JSON and skip AI generation.
  --no-ai              Print a local Rich text summary and skip AI generation.
  --markdown           Print Markdown and skip AI generation.
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
