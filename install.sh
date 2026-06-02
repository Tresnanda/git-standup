#!/usr/bin/env bash
set -e

APP_NAME="git-standup"
REPO_SLUG="Tresnanda/git-standup"
REPO_URL="https://github.com/$REPO_SLUG"
REPO_SPEC="git+https://github.com/Tresnanda/git-standup.git"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
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

python_version_ok() {
  "$1" - <<PY
import sys
raise SystemExit(0 if sys.version_info >= ($MIN_PYTHON_MAJOR, $MIN_PYTHON_MINOR) else 1)
PY
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      path="$(command -v "$candidate")"
      if python_version_ok "$path"; then
        echo "$path"
        return 0
      fi
    fi
  done
  return 1
}

data_home() {
  echo "${XDG_DATA_HOME:-$HOME/.local/share}"
}

pipx_bin_dir() {
  echo "${PIPX_BIN_DIR:-$HOME/.local/bin}"
}

bootstrap_pipx() {
  venv_dir="$(data_home)/$APP_NAME/pipx-bootstrap"
  log "pipx was not found; installing a private pipx helper..."
  mkdir -p "$(dirname "$venv_dir")"
  if ! "$PYTHON" -m venv "$venv_dir"; then
    log "Error: could not create a Python virtual environment for pipx."
    log "Install pipx manually, then rerun this installer."
    exit 1
  fi
  "$venv_dir/bin/python" -m pip install --upgrade pip pipx >/dev/null 2>&1
  PIPX=("$venv_dir/bin/pipx")
}

pipx_has_app() {
  "${PIPX[@]}" list --short 2>/dev/null | grep -Eq "^$APP_NAME([[:space:]]|$)"
}

install_or_update_app() {
  if pipx_has_app; then
    log "Updating existing $APP_NAME install..."
    "${PIPX[@]}" reinstall "$APP_NAME" --python "$PYTHON"
  else
    "${PIPX[@]}" install --python "$PYTHON" "$REPO_SPEC"
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

offer_star_repo() {
  if ! has_tty; then
    log "Star it here: $REPO_URL"
    return
  fi
  if ! ask_yes_no "If $APP_NAME helps you, star the GitHub repo now?" "y"; then
    log "Star it here: $REPO_URL"
    return
  fi
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if gh repo star "$REPO_SLUG" >/dev/null 2>&1; then
      log "[ok] Starred $REPO_URL"
      return
    fi
  fi
  if [ -n "${GITHUB_TOKEN:-}" ] && command -v curl >/dev/null 2>&1; then
    if curl -fsS -X PUT \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "https://api.github.com/user/starred/$REPO_SLUG" >/dev/null 2>&1; then
      log "[ok] Starred $REPO_URL"
      return
    fi
  fi
  log "Couldn't auto-star from this terminal."
  log "Star it here: $REPO_URL"
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
  has_tty || return 0
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
  has_tty || return 0
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
PYTHON="$(find_python)" || {
  log "Error: Python 3.10+ is required."
  if [ "$(uname -s 2>/dev/null)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    log "Install it with: brew install python"
  else
    log "Install Python 3.10 or newer, then rerun this installer."
  fi
  exit 1
}
log "[ok] Python: $("$PYTHON" --version 2>&1)"
command -v git >/dev/null 2>&1 && log "[ok] git found" || log "[warn] git not found"
command -v codex >/dev/null 2>&1 && log "[ok] Codex CLI found" || true

PIPX=()
if command -v pipx >/dev/null 2>&1; then
  pipx_path="$(command -v pipx)"
  if "$pipx_path" --version >/dev/null 2>&1; then
    PIPX=("$pipx_path")
    log "[ok] pipx found"
  else
    log "[info] Ignoring broken pipx at $pipx_path"
  fi
fi
if [ "${#PIPX[@]}" -eq 0 ] && "$PYTHON" -m pipx --version >/dev/null 2>&1; then
  PIPX=("$PYTHON" -m pipx)
  log "[ok] pipx found"
elif [ "${#PIPX[@]}" -eq 0 ] && ask_yes_no "Install pipx with this Python?" "y"; then
  bootstrap_pipx
elif [ "${#PIPX[@]}" -eq 0 ]; then
  log "Install pipx and rerun this installer."
  exit 1
fi

setup_ai_defaults

log "Installing $APP_NAME from GitHub..."
install_or_update_app
if command -v "$APP_NAME" >/dev/null 2>&1; then
  "$APP_NAME" --help >/dev/null
  log "[ok] $APP_NAME installed"
else
  log "[warn] $APP_NAME installed, but pipx bin dir may not be on PATH."
  log "Run: export PATH=\"$(pipx_bin_dir):\$PATH\""
fi

offer_star_repo
log "Run git-standup in your terminal to start the guided report builder."
