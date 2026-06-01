# AI Default Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent, non-secret AI defaults for git-standup.

**Architecture:** Add a small config module for reading/writing TOML-like defaults, wire it into the existing AI resolver, expose config commands, and let installers write the same file.

**Tech Stack:** Python, pytest, ruff, POSIX shell, PowerShell.

---

### Task 1: Config Module

**Files:**
- Create: `src/git_standup/config.py`
- Test: `tests/test_config.py`

- [ ] Add failing tests for config path, save/load, reset, and secret rejection.
- [ ] Implement the minimal config helpers.

### Task 2: Runtime and CLI Commands

**Files:**
- Modify: `src/git_standup/cli.py`
- Test: `tests/test_cli.py`

- [ ] Add failing tests for config command parsing and AI resolver using saved config.
- [ ] Wire config commands and runtime priority.

### Task 3: Installers and Docs

**Files:**
- Modify: `install.sh`
- Modify: `install.ps1`
- Modify: `README.md`

- [ ] Add installer prompt for default provider/model.
- [ ] Document how to show, change, and reset defaults.
