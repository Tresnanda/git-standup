# Simple Installer Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the git-standup installer with a simple numbered AI setup flow.

**Architecture:** Keep the flow inside `install.sh` and `install.ps1`; write provider defaults to config and keys to user env. Add static pytest coverage for the installer UX contract.

**Tech Stack:** Bash, PowerShell, pytest.

---

### Task 1: Installer Static Tests

**Files:**
- Create: `tests/test_installers.py`

- [ ] **Step 1: Write failing tests**

Create tests that read `install.sh` and `install.ps1`, then assert they include numbered AI choices, Codex, key entry, provider config writes, and no app-config key storage.

- [ ] **Step 2: Run tests to verify failure**

Run: `rtk .venv/bin/python -m pytest tests/test_installers.py`.

- [ ] **Step 3: Implement installer UX**

Modify `install.sh` and `install.ps1` with compact summaries, numbered provider choice, on-the-fly key entry, and safe config writes.

- [ ] **Step 4: Run tests and syntax checks**

Run: `rtk .venv/bin/python -m pytest tests/test_installers.py` and `rtk bash -n install.sh`.

### Task 2: Commit

**Files:**
- Modify: `install.sh`
- Modify: `install.ps1`
- Create: `tests/test_installers.py`

- [ ] **Step 1: Run full verification**

Run lint, tests, build checks, and installer syntax checks.

- [ ] **Step 2: Commit and push**

Commit with `feat: simplify installer wizard`.
