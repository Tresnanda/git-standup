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

log "Installing $APP_NAME from GitHub..."
"$PYTHON" -m pipx install --force "$REPO_SPEC"
if command -v "$APP_NAME" >/dev/null 2>&1; then
  "$APP_NAME" --help >/dev/null
  log "[ok] $APP_NAME installed"
else
  log "[warn] $APP_NAME installed, but pipx bin dir may not be on PATH."
  log "Run: python -m pipx ensurepath"
fi

if ask_yes_no "Run $APP_NAME wizard now?" "y"; then
  "$APP_NAME" wizard
fi
