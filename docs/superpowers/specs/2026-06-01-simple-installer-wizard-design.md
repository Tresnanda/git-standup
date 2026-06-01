# Simple Installer Wizard Design

## Goal

Make the `git-standup` curl installer easy for first-time users by replacing noisy AI diagnostics with a guided numbered setup.

## User Flow

The installer should:

1. Show a short welcome.
2. Check Python, Git, and pipx.
3. Show a compact AI summary, with found CLIs and keys grouped together.
4. Offer numbered AI default choices:
   - Codex CLI, when found, as the recommended default.
   - OpenAI API.
   - Gemini API.
   - OpenRouter API.
   - Skip AI setup.
5. If an API choice is missing its key, let the user paste the key now, show the env var command, or skip.
6. Save provider/model defaults in the app config file.
7. Save API keys only to shell/user environment, never to app config.
8. Install with pipx and optionally launch `git-standup wizard`.

## Secret Handling

API keys are written only to shell profiles on macOS/Linux or user-level environment variables on Windows. The app config stores `provider`, `base_url`, `model`, or `harness` only.

## Non-Interactive Mode

`--yes` keeps installation unattended and skips prompts, API key entry, and wizard launch.

## Testing

Use static installer tests for numbered choices, key entry, Codex selection, and config/secret separation. Use shell syntax checks for `install.sh`.
