#!/usr/bin/env bash
set -e

APP_NAME="git-standup"
REPO_SPEC="git+https://github.com/Tresnanda/git-standup.git"
YES=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    -h|--help)
      echo "Usage: install.sh [--yes]"
      exit 0
      ;;
  esac
done

log() { printf '%s\n' "$*"; }
has_tty() { [ "$YES" -eq 0 ] && [ -r /dev/tty ]; }

ask_yes_no() {
  prompt="$1"
  default="${2:-y}"
  if ! has_tty; then
    [ "$default" = "y" ]
    return
  fi
  if [ "$default" = "y" ]; then suffix="[Y/n]"; else suffix="[y/N]"; fi
  printf '%s %s ' "$prompt" "$suffix" >/dev/tty
  read -r answer </dev/tty || answer=""
  answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
  [ -z "$answer" ] && answer="$default"
  [ "$answer" = "y" ] || [ "$answer" = "yes" ]
}

ask_choice() {
  prompt="$1"
  default="$2"
  if ! has_tty; then
    echo "$default"
    return
  fi
  printf '%s [%s]: ' "$prompt" "$default" >/dev/tty
  read -r answer </dev/tty || answer=""
  if [ -n "$answer" ]; then echo "$answer"; else echo "$default"; fi
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then command -v python3
  elif command -v python >/dev/null 2>&1; then command -v python
  else return 1
  fi
}

shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

shell_profile() {
  if [ -n "${ZDOTDIR:-}" ] && [ -d "$ZDOTDIR" ]; then
    echo "$ZDOTDIR/.zshrc"
  elif [ -n "${SHELL:-}" ] && [ "${SHELL##*/}" = "bash" ]; then
    echo "$HOME/.bashrc"
  else
    echo "$HOME/.zshrc"
  fi
}

save_secret_to_shell_profile() {
  name="$1"
  value="$2"
  profile="$(shell_profile)"
  mkdir -p "$(dirname "$profile")"
  {
    printf '\n# Added by %s installer\n' "$APP_NAME"
    printf 'export %s=%s\n' "$name" "$(shell_quote "$value")"
  } >>"$profile"
  export "$name=$value"
  log "[ok] Saved $name to $profile"
  log "Open a new terminal or run: source $profile"
}

config_dir() {
  echo "${XDG_CONFIG_HOME:-$HOME/.config}/$APP_NAME"
}

write_provider_config() {
  provider="$1"
  base_url="$2"
  model="$3"
  dir="$(config_dir)"
  mkdir -p "$dir"
  {
    printf '# %s AI defaults. Store API keys in environment variables, not here.\n' "$APP_NAME"
    printf 'provider = "%s"\n' "$provider"
    printf 'base_url = "%s"\n' "$base_url"
    printf 'model = "%s"\n' "$model"
  } >"$dir/config.toml"
  log "[ok] Saved AI default to $dir/config.toml"
}

write_codex_config() {
  dir="$(config_dir)"
  mkdir -p "$dir"
  {
    printf '# %s AI defaults. Store API keys in environment variables, not here.\n' "$APP_NAME"
    printf 'harness = "codex"\n'
  } >"$dir/config.toml"
  log "[ok] Saved Codex as the AI default"
}

provider_env_key() {
  case "$1" in
    openai) echo "OPENAI_API_KEY" ;;
    gemini) echo "GEMINI_API_KEY" ;;
    openrouter) echo "OPENROUTER_API_KEY" ;;
  esac
}

provider_base_url() {
  case "$1" in
    gemini) echo "https://generativelanguage.googleapis.com/v1beta/openai/" ;;
    openrouter) echo "https://openrouter.ai/api/v1" ;;
    *) echo "${OPENAI_BASE_URL:-https://api.openai.com/v1}" ;;
  esac
}

provider_model() {
  case "$1" in
    gemini) echo "gemini-3.5-flash" ;;
    openrouter) echo "openai/gpt-4o-mini" ;;
    *) echo "gpt-4o-mini" ;;
  esac
}

ensure_provider_key() {
  provider="$1"
  key_name="$(provider_env_key "$provider")"
  eval "key_value=\${$key_name:-}"
  [ -n "$key_value" ] && { log "[ok] $key_name already set"; return; }
  has_tty || return
  log ""
  log "$key_name was not found."
  log "1) Paste API key now"
  log "2) Show me the env var command"
  log "3) Skip key setup"
  choice="$(ask_choice "Choice" "1")"
  case "$choice" in
    1)
      printf 'Enter %s: ' "$key_name" >/dev/tty
      stty -echo </dev/tty 2>/dev/null || true
      read -r api_key </dev/tty || api_key=""
      stty echo </dev/tty 2>/dev/null || true
      printf '\n' >/dev/tty
      if [ -n "$api_key" ]; then
        save_secret_to_shell_profile "$key_name" "$api_key"
      else
        log "[info] Empty key skipped"
      fi
      ;;
    2)
      log "Run this later:"
      log "  export $key_name=\"your-api-key\""
      ;;
    *)
      log "[info] Skipped API key setup"
      ;;
  esac
}

default_ai_choice() {
  if command -v codex >/dev/null 2>&1; then echo "1"
  elif [ -n "${OPENAI_API_KEY:-}" ]; then echo "2"
  elif [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${GOOGLE_API_KEY:-}" ]; then echo "3"
  elif [ -n "${OPENROUTER_API_KEY:-}" ]; then echo "4"
  else echo "5"
  fi
}

setup_ai_defaults() {
  has_tty || return
  log ""
  log "Choose AI default:"
  log "1) Codex CLI - recommended if you use Codex"
  log "2) OpenAI API"
  log "3) Gemini API"
  log "4) OpenRouter API"
  log "5) Skip AI setup"
  choice="$(ask_choice "Choice" "$(default_ai_choice)")"
  case "$choice" in
    1) write_codex_config ;;
    2) ensure_provider_key "openai"; write_provider_config "openai" "$(provider_base_url openai)" "$(provider_model openai)" ;;
    3) ensure_provider_key "gemini"; write_provider_config "gemini" "$(provider_base_url gemini)" "$(provider_model gemini)" ;;
    4) ensure_provider_key "openrouter"; write_provider_config "openrouter" "$(provider_base_url openrouter)" "$(provider_model openrouter)" ;;
    *) log "[info] Skipped AI setup. You can run: $APP_NAME config" ;;
  esac
}

log "Install git-standup"
log "This checks Python/Git, installs with pipx, and can set an AI default."
PYTHON="$(find_python)" || { log "Error: Python 3.10+ is required."; exit 1; }
log "[ok] Python: $("$PYTHON" --version 2>&1)"
command -v git >/dev/null 2>&1 && log "[ok] git found" || log "[warn] git not found"
command -v codex >/dev/null 2>&1 && log "[ok] Codex CLI found" || true

if "$PYTHON" -m pipx --version >/dev/null 2>&1; then
  log "[ok] pipx found"
elif ask_yes_no "Install pipx with this Python?" "y"; then
  "$PYTHON" -m pip install --user pipx
  "$PYTHON" -m pipx ensurepath >/dev/null 2>&1 || true
else
  log "Install pipx and rerun this installer."
  exit 1
fi

setup_ai_defaults

log "Installing $APP_NAME from GitHub..."
"$PYTHON" -m pipx install --force "$REPO_SPEC"
if command -v "$APP_NAME" >/dev/null 2>&1; then
  "$APP_NAME" --help >/dev/null
  log "[ok] $APP_NAME installed"
else
  log "[warn] $APP_NAME installed, but pipx bin dir may not be on PATH."
  log "Run: python -m pipx ensurepath"
fi

if has_tty && ask_yes_no "Run $APP_NAME wizard now?" "y"; then
  "$APP_NAME" wizard
fi
