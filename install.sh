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
find_python() {
  if command -v python3 >/dev/null 2>&1; then command -v python3
  elif command -v python >/dev/null 2>&1; then command -v python
  else return 1
  fi
}
check_keys() {
  for key in OPENAI_API_KEY OPENAI_BASE_URL GEMINI_API_KEY GOOGLE_API_KEY GROQ_API_KEY MISTRAL_API_KEY OPENROUTER_API_KEY TOGETHER_API_KEY PERPLEXITY_API_KEY XAI_API_KEY AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT; do
    eval "value=\${$key:-}"
    if [ -n "$value" ]; then log "[ok] $key is set"; else log "[info] $key not set"; fi
  done
}
check_commands() {
  for cmd in openai gemini ollama lms gh vercel aider opencode codex; do
    if command -v "$cmd" >/dev/null 2>&1; then log "[ok] $cmd CLI found"; fi
  done
}
check_endpoints() {
  command -v curl >/dev/null 2>&1 || return
  for url in http://localhost:11434/v1/models http://localhost:1234/v1/models http://localhost:4000/v1/models; do
    if curl -fsS --max-time 1 "$url" >/dev/null 2>&1; then log "[ok] local endpoint: ${url%/models}"; fi
  done
}
default_provider() {
  if [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${GOOGLE_API_KEY:-}" ]; then echo "gemini"
  elif [ -n "${OPENROUTER_API_KEY:-}" ]; then echo "openrouter"
  elif [ -n "${GROQ_API_KEY:-}" ]; then echo "groq"
  elif [ -n "${MISTRAL_API_KEY:-}" ]; then echo "mistral"
  elif [ -n "${TOGETHER_API_KEY:-}" ]; then echo "together"
  elif [ -n "${PERPLEXITY_API_KEY:-}" ]; then echo "perplexity"
  elif [ -n "${XAI_API_KEY:-}" ]; then echo "xai"
  else echo "openai"
  fi
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
ask_text() {
  prompt="$1"
  default="$2"
  printf '%s [%s] ' "$prompt" "$default" >/dev/tty
  read -r answer </dev/tty || answer=""
  if [ -n "$answer" ]; then echo "$answer"; else echo "$default"; fi
}
write_ai_defaults() {
  has_tty || return
  ask_yes_no "Save default AI provider/model for $APP_NAME?" "y" || return
  provider="$(ask_text "Provider (openai/gemini/openrouter/groq/mistral/together/perplexity/xai)" "$(default_provider)")"
  base_url="$(ask_text "Base URL" "$(provider_base_url "$provider")")"
  model="$(ask_text "Model" "$(provider_model "$provider")")"
  config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/$APP_NAME"
  mkdir -p "$config_dir"
  {
    printf '# %s AI defaults. Store API keys in environment variables, not here.\n' "$APP_NAME"
    printf 'provider = "%s"\n' "$provider"
    printf 'base_url = "%s"\n' "$base_url"
    printf 'model = "%s"\n' "$model"
  } >"$config_dir/config.toml"
  log "[ok] Saved AI defaults to $config_dir/config.toml"
}

log "git-standup installer"
PYTHON="$(find_python)" || { log "Error: Python 3.10+ is required."; exit 1; }
log "[ok] Python: $("$PYTHON" --version 2>&1)"
command -v git >/dev/null 2>&1 && log "[ok] git found" || log "[warn] git not found"

if "$PYTHON" -m pipx --version >/dev/null 2>&1; then
  log "[ok] pipx found"
elif ask_yes_no "Install pipx with this Python?" "y"; then
  "$PYTHON" -m pip install --user pipx
  "$PYTHON" -m pipx ensurepath >/dev/null 2>&1 || true
else
  log "Install pipx and rerun this installer."
  exit 1
fi

log "AI environment checks (Claude/Anthropic intentionally skipped):"
check_keys
check_commands
check_endpoints
write_ai_defaults

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
