# Changelog

All notable changes to git-standup are documented here.

## Unreleased

- Added easy presets: `git-standup me`, `git-standup week`, and `git-standup branch`.
- Added positional repository paths, so `git-standup ../repo --markdown` works without `--repo`.
- Added `--out` as a shorter alias for `--output`.

## 0.1.0 - 2026-06-01

- Added Git history summarization grouped by author and date.
- Added local text, JSON, Markdown, and AI-generated summary modes.
- Added branch comparison with `--base-branch`.
- Added `--repo` for running against another repository path.
- Added exact reporting windows with `--since` and `--until`.
- Added `--output` for writing JSON, Markdown, text, or AI summaries to files.
- Added multiline commit body parsing and binary `--numstat` handling.
- Added tests, linting, packaging metadata, and CI.
