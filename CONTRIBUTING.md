# Contributing to git-standup

Thanks for improving git-standup. The project should stay useful in both low-friction local workflows and scripted reporting jobs.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Before Opening a Pull Request

Run:

```bash
ruff check .
pytest
python -m pip_audit
python -m build
python -m twine check dist/*
```

If you build locally, remove generated `build/`, `dist/`, and `*.egg-info` directories before committing.

## Contribution Guidelines

- Add tests for Git command construction, log parsing, formatter output, and CLI behavior changes.
- Keep JSON output stable because users may feed it into dashboards or release tooling.
- Preserve the no-AI path. The tool must remain useful without an API key.
- Avoid sending more data to AI providers than necessary for a useful summary.
- Document new CLI flags and workflows in `README.md`.

## Reporting Bugs

Please include:

- The command you ran.
- Whether the repository uses merge commits, squash commits, rebases, or unusual Git history.
- A redacted sample of the relevant `git log --numstat` output when possible.
- Your Python and Git versions.
