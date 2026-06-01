# AI Default Config Design

## Goal

Let users choose a default AI provider or local CLI harness during installation, then change that default later without editing shell profiles or passing flags every run.

## Behavior

- Store non-secret defaults in user config, not the project repo.
- Do not store API keys.
- Resolve runtime settings in this order: explicit CLI flags, user config, detected environment, built-in default.
- Add `config`, `config show`, `config reset`, and `config set-provider` commands.
- The install wizard should offer to save detected AI defaults after installation.

## Config Location

- macOS/Linux: `$XDG_CONFIG_HOME/git-standup/config.toml` or `~/.config/git-standup/config.toml`.
- Windows: `%APPDATA%\git-standup\config.toml`.

## Config Shape

```toml
provider = "gemini"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-3.5-flash"
```

## Safety

The config file may contain provider, base URL, model, and optional harness name. It must not contain API keys or secret values.
