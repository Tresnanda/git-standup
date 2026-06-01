# git-standup 🚀

**AI-powered weekly standup generator** — analyzes your git log and generates standup summaries with AI (or plain text).

Works in any git repository. Supports OpenAI-compatible APIs.

## Features

- 📊 **Grouped output** — commits organized by author and date
- 🤖 **AI summaries** — natural-language standup via LLM (OpenAI-compatible APIs)
- 📝 **Text mode** — pretty terminal output without AI (`--no-ai`)
- 🔍 **JSON export** — raw structured data for pipelines (`--json`)
- 👤 **Filter by author** — `--author me` or any name
- 📅 **Custom range** — `--days 1` for yesterday, `--days 14` for two weeks
- 🔀 **Branch comparison** — `--base-branch main` to show just your branch changes
- 🔌 **Any API** — OpenAI, Azure, Ollama, LocalAI, etc. via `--base-url`

## Installation

```bash
# From source
cd git-standup
pip install .

# Or with uv
uv pip install .
```

## Usage

### Basic

```bash
# Last 7 days, all contributors
git-standup

# Yesterday only
git-standup --days 1

# My commits only
git-standup --author me

# Filter by specific author
git-standup --author "Jane Doe"
```

### Output modes

```bash
# AI-generated standup (requires API key)
git-standup --api-key sk-...

# Text summary without AI
git-standup --no-ai

# Raw JSON
git-standup --json
```

### AI configuration

```bash
# Custom model
git-standup --model gpt-4

# Custom API endpoint (e.g., Azure, Ollama, LocalAI)
git-standup --base-url https://api.openai.com/v1

# Or set environment variables
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
git-standup
```

### Branch-specific

```bash
# Show commits in current branch not in main
git-standup --base-branch main
```

## Examples

### Text mode output

```
📋 Weekly Standup Summary

👤 Alice
──────────────────────────────────────────────────
  📅 Mon Mar 10, 2026
    [abc1234] Add user authentication middleware
      ├ app/middleware/auth.py (+156/-12)
      ├ tests/test_auth.py (+89/-0)
      └ docs/auth.md (+45/-0)
    [def5678] Fix rate limiting bug
      ├ app/middleware/rate_limit.py (+12/-8)
      └ tests/test_rate_limit.py (+34/-0)
```

### AI mode output

```
🤖 AI-Generated Standup

This week, Alice worked on adding user authentication middleware
(+156 lines) with comprehensive test coverage. Bob refactored the
database layer to improve query performance, resulting in ~40% faster
reads. Carol fixed two critical bugs in the payment processing pipeline
and added regression tests.
```

### JSON output

```json
{
  "Alice": {
    "2026-03-10": {
      "commits": [
        {
          "hash": "abc1234...",
          "subject": "Add user authentication middleware",
          "files": [...]
        }
      ],
      "stats": {
        "total_commits": 1,
        "total_insertions": 290,
        "total_deletions": 12,
        "total_files": 3
      }
    }
  }
}
```

## Requirements

- Python 3.9+
- git
- Dependencies: `gitpython`, `rich`, `httpx` (installed automatically)

## License

MIT
