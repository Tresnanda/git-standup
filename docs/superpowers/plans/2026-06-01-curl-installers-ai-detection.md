# Curl Installers and AI Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-line installers and safe environment detection across the CLIs.

**Architecture:** Each repo owns its own installer scripts and README commands. The AI projects add small Python detection helpers so tests cover provider/key classification; shell installers use matching conservative checks.

**Tech Stack:** Python, pytest, ruff, POSIX shell, PowerShell, pipx.

---

### Task 1: Add AI Environment Detection

**Files:**
- Create: `src/git_standup/ai_env.py`
- Test: `tests/test_ai_env.py`
- Modify: `src/git_standup/cli.py`

- [ ] Write tests for key detection, command detection, and provider connection resolution.
- [ ] Implement helpers for OpenAI-compatible provider defaults and safe detection output.
- [ ] Use the resolver before AI generation.

### Task 2: Add Installer Scripts

**Files:**
- Create: `install.sh`
- Create: `install.ps1`
- Modify: `README.md`

- [ ] Add scripts that detect Python, pipx, API keys, local CLIs, and local endpoints.
- [ ] Add README curl/PowerShell install snippets.
- [ ] Verify shell syntax with `bash -n install.sh`.

### Task 3: Verify Package

**Files:**
- Existing Python package and tests.

- [ ] Run `python -m ruff check .`.
- [ ] Run `python -m pytest`.
- [ ] Run `python -m build`.
- [ ] Run `python -m twine check dist/*`.
