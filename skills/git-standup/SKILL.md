---
name: git-standup
description: Use when turning Git history into standup reports, weekly updates, changelogs, PR summaries, OSS activity logs, or multi-repository development summaries with the git-standup CLI.
metadata:
  author: Tresnanda
  project: https://github.com/Tresnanda/git-standup
---

# git-standup

Use this skill when the user asks for a standup, weekly update, daily summary,
branch activity report, changelog draft, OSS activity log, or multi-repository
development summary.

## Core Rule

Use the `git-standup` CLI as the source of truth for commit selection,
grouping, author filtering, and report formatting. Do not manually reconstruct
the report with ad hoc `git log` commands unless the CLI is unavailable.

## Before Running

1. Locate the repository or repositories to report on.
2. Check whether `git-standup` is installed:

```bash
git-standup --help
```

3. If it is not installed, suggest:

```bash
curl -fsSL https://raw.githubusercontent.com/Tresnanda/git-standup/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/Tresnanda/git-standup/main/install.ps1 | iex
```

## Choosing The Right Report

Use these commands as defaults:

```bash
git-standup --no-wizard --markdown
```

Last 7 days:

```bash
git-standup --no-wizard --days 7 --markdown
```

Today only:

```bash
git-standup --no-wizard --since YYYY-MM-DD --markdown
```

Only the current user's commits:

```bash
git-standup --no-wizard --author me --markdown
```

Specific authors:

```bash
git-standup --no-wizard --author "Alice|Bob" --markdown
```

Branch comparison:

```bash
git-standup --no-wizard --base-branch main --markdown
```

Specific paths:

```bash
git-standup --no-wizard --path src --path tests --markdown
```

Another local repository:

```bash
git-standup --no-wizard --repo /path/to/repo --markdown
```

Multiple GitHub repositories:

```bash
git-standup --no-wizard --remote-repo owner/api --remote-repo owner/web --markdown
```

## Output Modes

- `--markdown`: best for GitHub, Slack, Notion, OSS updates, and PR notes.
- `--no-ai`: use when the user wants raw local output or no network/API use.
- `--json`: use for automation, scripts, dashboards, or agent parsing.
- `--stats-only`: use for compact aggregate counts without per-commit details.
- `--changelog`: use for release-note style Markdown grouped by conventional
  commit type.
- `--output <file>`: use when the user wants the report saved.

If the user asks for terminal output that is easy to read, prefer Markdown and
let git-standup render it in the terminal. If the user asks for copy/paste
content, preserve the raw Markdown.

## AI Usage

git-standup can use OpenAI-compatible providers or local CLI harnesses. If the
user has Codex CLI configured, prefer the saved Codex CLI harness when available
instead of asking for a new API key.

Useful setup command:

```bash
git-standup config
```

Show saved defaults:

```bash
git-standup config show
```

Do not ask for Claude/Anthropic setup; git-standup intentionally skips that in
installer detection.

## Interactive Wizard

When the user wants to choose options manually, run:

```bash
git-standup
```

The wizard supports:

- current directory, another local directory, or remote GitHub repositories
- today, last 7 days, custom range, or branch comparison
- everyone, current user, or selected authors
- Markdown, plain text, JSON, or changelog output
- file saving
- long author/repository pickers with a bounded viewport

In the wizard, `q` cancels. Do not treat cancellation as a selected default.

## Response Style

When presenting a report:

- Keep the summary concise and useful for a human standup.
- Preserve authors and repository headings when reporting across multiple repos.
- Mention the exact command used when helpful.
- Avoid claiming work that is not represented in commits.
- If no commits are found, explain the likely filter that caused it: date range,
  author, repository path, branch, or path filters.
