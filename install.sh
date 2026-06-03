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

harness_label() {
  case "$1" in
    codex) echo "Codex CLI" ;;
    cursor-agent) echo "Cursor Agent" ;;
    agent) echo "Cursor Agent" ;;
    opencode) echo "OpenCode" ;;
    gemini) echo "Gemini CLI" ;;
    aider) echo "Aider" ;;
    goose) echo "Goose" ;;
    copilot) echo "GitHub Copilot CLI" ;;
    kiro-cli) echo "Kiro CLI" ;;
    amp) echo "Amp" ;;
    ollama) echo "Ollama" ;;
    lms) echo "LM Studio" ;;
  esac
}

write_harness_config() {
  harness="$1"
  dir="$(config_dir)"
  mkdir -p "$dir"
  {
    printf '# %s AI defaults. Store API keys in environment variables, not here.\n' "$APP_NAME"
    printf 'harness = "%s"\n' "$harness"
  } >"$dir/config.toml"
  log "[ok] Saved $(harness_label "$harness") as the AI default"
}

provider_label() {
  case "$1" in
    openai) echo "OpenAI API" ;;
    gemini) echo "Gemini API" ;;
    openrouter) echo "OpenRouter API" ;;
    groq) echo "Groq API" ;;
    mistral) echo "Mistral API" ;;
    together) echo "Together AI API" ;;
    perplexity) echo "Perplexity API" ;;
    xai) echo "xAI API" ;;
  esac
}

provider_env_keys() {
  case "$1" in
    openai) echo "OPENAI_API_KEY" ;;
    gemini) echo "GEMINI_API_KEY GOOGLE_API_KEY" ;;
    openrouter) echo "OPENROUTER_API_KEY" ;;
    groq) echo "GROQ_API_KEY" ;;
    mistral) echo "MISTRAL_API_KEY" ;;
    together) echo "TOGETHER_API_KEY" ;;
    perplexity) echo "PERPLEXITY_API_KEY" ;;
    xai) echo "XAI_API_KEY" ;;
  esac
}

provider_base_url() {
  case "$1" in
    gemini) echo "https://generativelanguage.googleapis.com/v1beta/openai/" ;;
    openrouter) echo "https://openrouter.ai/api/v1" ;;
    groq) echo "https://api.groq.com/openai/v1" ;;
    mistral) echo "https://api.mistral.ai/v1" ;;
    together) echo "https://api.together.xyz/v1" ;;
    perplexity) echo "https://api.perplexity.ai" ;;
    xai) echo "https://api.x.ai/v1" ;;
    *) echo "${OPENAI_BASE_URL:-https://api.openai.com/v1}" ;;
  esac
}

provider_model() {
  case "$1" in
    gemini) echo "gemini-3.5-flash" ;;
    openrouter) echo "openai/gpt-4o-mini" ;;
    groq) echo "llama-3.3-70b-versatile" ;;
    mistral) echo "mistral-small-latest" ;;
    together) echo "meta-llama/Llama-3.3-70B-Instruct-Turbo" ;;
    perplexity) echo "sonar-pro" ;;
    xai) echo "grok-3-mini" ;;
    *) echo "gpt-4o-mini" ;;
  esac
}

ensure_provider_key() {
  provider="$1"
  key_names="$(provider_env_keys "$provider")"
  primary_key="${key_names%% *}"
  for key_name in $key_names; do
    eval "key_value=\${$key_name:-}"
    [ -n "$key_value" ] && { log "[ok] $key_name already set"; return; }
  done
  has_tty || return 0
  log ""
  log "$primary_key was not found."
  log "1) Paste API key now"
  log "2) Show me the env var command"
  log "3) Skip key setup"
  choice="$(ask_choice "Choice" "1")"
  case "$choice" in
    1)
      printf 'Enter %s: ' "$primary_key" >/dev/tty
      stty -echo </dev/tty 2>/dev/null || true
      read -r api_key </dev/tty || api_key=""
      stty echo </dev/tty 2>/dev/null || true
      printf '\n' >/dev/tty
      if [ -n "$api_key" ]; then
        save_secret_to_shell_profile "$primary_key" "$api_key"
      else
        log "[info] Empty key skipped"
      fi
      ;;
    2)
      log "Run this later:"
      log "  export $primary_key=\"your-api-key\""
      ;;
    *)
      log "[info] Skipped API key setup"
      ;;
  esac
}

default_ai_choice() {
  if command -v codex >/dev/null 2>&1; then echo "1"
  elif command -v cursor-agent >/dev/null 2>&1; then echo "2"
  elif command -v agent >/dev/null 2>&1; then echo "3"
  elif command -v opencode >/dev/null 2>&1; then echo "4"
  elif command -v gemini >/dev/null 2>&1; then echo "5"
  elif command -v aider >/dev/null 2>&1; then echo "6"
  elif command -v goose >/dev/null 2>&1; then echo "7"
  elif command -v copilot >/dev/null 2>&1; then echo "8"
  elif command -v kiro-cli >/dev/null 2>&1; then echo "9"
  elif command -v amp >/dev/null 2>&1; then echo "10"
  elif command -v ollama >/dev/null 2>&1; then echo "11"
  elif command -v lms >/dev/null 2>&1; then echo "12"
  elif [ -n "${OPENAI_API_KEY:-}" ]; then echo "13"
  elif [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${GOOGLE_API_KEY:-}" ]; then echo "14"
  elif [ -n "${OPENROUTER_API_KEY:-}" ]; then echo "15"
  elif [ -n "${GROQ_API_KEY:-}" ]; then echo "16"
  elif [ -n "${MISTRAL_API_KEY:-}" ]; then echo "17"
  elif [ -n "${TOGETHER_API_KEY:-}" ]; then echo "18"
  elif [ -n "${PERPLEXITY_API_KEY:-}" ]; then echo "19"
  elif [ -n "${XAI_API_KEY:-}" ]; then echo "20"
  else echo "21"
  fi
}

setup_ai_defaults() {
  has_tty || return 0
  log ""
  log "Choose AI default:"
  log "1) $(harness_label codex)"
  log "2) $(harness_label cursor-agent)"
  log "3) $(harness_label agent)"
  log "4) $(harness_label opencode)"
  log "5) $(harness_label gemini)"
  log "6) $(harness_label aider)"
  log "7) $(harness_label goose)"
  log "8) $(harness_label copilot)"
  log "9) $(harness_label kiro-cli)"
  log "10) $(harness_label amp)"
  log "11) $(harness_label ollama)"
  log "12) $(harness_label lms)"
  log "13) $(provider_label openai)"
  log "14) $(provider_label gemini)"
  log "15) $(provider_label openrouter)"
  log "16) $(provider_label groq)"
  log "17) $(provider_label mistral)"
  log "18) $(provider_label together)"
  log "19) $(provider_label perplexity)"
  log "20) $(provider_label xai)"
  log "21) Skip AI setup"
  choice="$(ask_choice "Choice" "$(default_ai_choice)")"
  case "$choice" in
    1) write_harness_config "codex" ;;
    2) write_harness_config "cursor-agent" ;;
    3) write_harness_config "agent" ;;
    4) write_harness_config "opencode" ;;
    5) write_harness_config "gemini" ;;
    6) write_harness_config "aider" ;;
    7) write_harness_config "goose" ;;
    8) write_harness_config "copilot" ;;
    9) write_harness_config "kiro-cli" ;;
    10) write_harness_config "amp" ;;
    11) write_harness_config "ollama" ;;
    12) write_harness_config "lms" ;;
    13) ensure_provider_key "openai"; write_provider_config "openai" "$(provider_base_url openai)" "$(provider_model openai)" ;;
    14) ensure_provider_key "gemini"; write_provider_config "gemini" "$(provider_base_url gemini)" "$(provider_model gemini)" ;;
    15) ensure_provider_key "openrouter"; write_provider_config "openrouter" "$(provider_base_url openrouter)" "$(provider_model openrouter)" ;;
    16) ensure_provider_key "groq"; write_provider_config "groq" "$(provider_base_url groq)" "$(provider_model groq)" ;;
    17) ensure_provider_key "mistral"; write_provider_config "mistral" "$(provider_base_url mistral)" "$(provider_model mistral)" ;;
    18) ensure_provider_key "together"; write_provider_config "together" "$(provider_base_url together)" "$(provider_model together)" ;;
    19) ensure_provider_key "perplexity"; write_provider_config "perplexity" "$(provider_base_url perplexity)" "$(provider_model perplexity)" ;;
    20) ensure_provider_key "xai"; write_provider_config "xai" "$(provider_base_url xai)" "$(provider_model xai)" ;;
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
