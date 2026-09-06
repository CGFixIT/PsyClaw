#!/usr/bin/env bash
# setup-from-clone.sh — one-shot Apple Silicon setup AFTER `git clone`.
#
# Closes the gap setup-guide.md documents between Option A (install-cyclaw.sh)
# and Option B (by-hand): neither of those alone does keys + Ollama + index +
# both servers. This orchestrator does. It does NOT reimplement the existing
# macos/ scripts — it chains them, then fills the four holes they leave:
#
#   install-cyclaw.sh      home layout, venv, torch (plain 2.13.0), shim
#   setup-cyclaw-keys.sh   CYCLAW_API_KEY + Telegram / Claude / Grok / GitHub
#   (this script)          Python 3.12 offer, brew analytics off, Ollama,
#                          retrieval index, gh login hint, start both servers
#   invoke-cyclaw.sh       RAG gateway :8787 + harness :8790
#
# Usage (from the checkout root, after git clone):
#   bash macos/setup-from-clone.sh
#   bash macos/setup-from-clone.sh --skip-ollama --no-start
#   bash macos/setup-from-clone.sh --skip-prompts --grok-dummy --no-start
#   bash macos/setup-from-clone.sh --dry-run
#
# Options:
#   --skip-install        skip macos/install-cyclaw.sh (venv already exists)
#   --skip-python-deps    pass through to the installer
#   --skip-keys           do not run setup-cyclaw-keys.sh
#   --skip-ollama         do not check / pull a local model
#   --skip-index          do not run python -m retrieval.indexer
#   --skip-advisor        do not run .claude/skills/cyclaw-advisor/verify.sh
#   --no-start            do not launch the terminal + harness servers
#   --start               launch servers even under --skip-prompts
#   --no-browser          pass --no-browser to invoke-cyclaw.sh
#   --no-fsconnect        pass --no-fsconnect to the installer
#   --no-profile-edit     pass through to installer + keys
#   --no-path-edit        pass through to the installer
#   --grok-dummy          pass --grok-dummy to setup-cyclaw-keys.sh
#   --rotate-key          pass --rotate to setup-cyclaw-keys.sh
#   --small-model         pull/use qwen2.5:7b instead of the shipped 27B default
#   --ollama-model TAG    pull this Ollama tag (overrides --small-model)
#   --ollama-install-script
#                         allow the unsigned ollama.com/install.sh pipe
#                         (default: open the signed .app download page)
#   --skip-prompts        non-interactive: skip offers, do not start servers
#                         unless --start, do not pull a multi-GB model
#   --dry-run             print the plan and exit 0 (no writes, no network)
#   --help
#
# Target: macOS 14+ Apple Silicon (arm64), bash 3.2 / zsh, BSD userland.
# No Homebrew required. Tests set CYCLAW_SETUP_FROM_CLONE_SKIP_PLATFORM=1.
#
# Privacy (cyclaw-advisor SKILL.md):
#   never log secret values
#   never write them to config.yaml
#   never put them in argv of a child we do not control
#   never inline them into ~/.zshrc / ~/.bash_profile
#   Keychain + ~/.CyClaw/.env (chmod 600) is the only persist path
#   fsconnect writes/indexing stay off
#   I3: do not flip app.mode or models.*.enabled (triple-gated fallback stays)
#   I5: do not touch soul.md
#   I6: this file is installer glue — gate.py / graph.py never import it
#   do not run cyclaw-advisor/bootstrap.sh (it git-fetches origin/main)
#
# What this script will NOT do (deliberate):
#   - curl | sh Homebrew or Ollama unless you pass --ollama-install-script
#   - enable fsconnect writes or indexing
#   - generate / load LaunchAgents (those need --confirm --reason)
#   - flip app.mode or models.*.enabled in config.yaml
#   - nltk.download() (punkt is unused; retrieval/stemmer.py is regex-only)

set -euo pipefail

usage() {
  sed -n '3,66p' "$0" | sed 's/^# \{0,1\}//'
}

step() { printf '[cyclaw] %s\n' "$1"; }
warn() { printf '[cyclaw] WARNING: %s\n' "$1" >&2; }
die()  { printf '[cyclaw] error: %s\n' "$1" >&2; exit 1; }

SKIP_INSTALL=0
SKIP_PYTHON_DEPS=0
SKIP_KEYS=0
SKIP_OLLAMA=0
SKIP_INDEX=0
SKIP_ADVISOR=0
NO_START=0
FORCE_START=0
NO_BROWSER=0
NO_FSCONNECT=0
NO_PROFILE=0
NO_PATH=0
GROK_DUMMY=0
ROTATE_KEY=0
SMALL_MODEL=0
OLLAMA_MODEL=""
OLLAMA_INSTALL_SCRIPT=0
SKIP_PROMPTS=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-install) SKIP_INSTALL=1; shift ;;
    --skip-python-deps) SKIP_PYTHON_DEPS=1; shift ;;
    --skip-keys) SKIP_KEYS=1; shift ;;
    --skip-ollama) SKIP_OLLAMA=1; shift ;;
    --skip-index) SKIP_INDEX=1; shift ;;
    --skip-advisor) SKIP_ADVISOR=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --start) FORCE_START=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --no-fsconnect) NO_FSCONNECT=1; shift ;;
    --no-profile-edit) NO_PROFILE=1; shift ;;
    --no-path-edit) NO_PATH=1; shift ;;
    --grok-dummy) GROK_DUMMY=1; shift ;;
    --rotate-key) ROTATE_KEY=1; shift ;;
    --small-model) SMALL_MODEL=1; shift ;;
    --ollama-model) OLLAMA_MODEL="${2:?--ollama-model requires a tag}"; shift 2 ;;
    --ollama-install-script) OLLAMA_INSTALL_SCRIPT=1; shift ;;
    --skip-prompts|--yes) SKIP_PROMPTS=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# -- platform -----------------------------------------------------------------

_require_platform() {
  if [ "${CYCLAW_SETUP_FROM_CLONE_SKIP_PLATFORM:-}" = "1" ]; then
    return 0
  fi
  if [ "$(uname -s)" != "Darwin" ]; then
    die "this script is for macOS Apple Silicon only (uname -s is $(uname -s))."
  fi
  if [ "$(uname -m)" != "arm64" ]; then
    echo "[cyclaw] Apple Silicon (arm64) required; this Mac reports $(uname -m)." >&2
    echo "[cyclaw] Intel Macs cannot satisfy CyClaw's pinned torch wheel. See setup-guide.md." >&2
    exit 1
  fi
  local ver major
  ver="$(sw_vers -productVersion 2>/dev/null || true)"
  major="${ver%%.*}"
  if [ -n "$major" ] && [ "$major" -lt 14 ] 2>/dev/null; then
    warn "macOS 14 Sonoma is the CyClaw floor (detected ${ver}). The torch wheel is macosx_14_0_arm64."
  fi
}

_require_platform

if [ "$SKIP_PROMPTS" -eq 0 ] && [ "$DRY_RUN" -eq 0 ] && [ ! -t 0 ]; then
  die "interactive prompts need a TTY. Re-run in a terminal, or pass --skip-prompts."
fi

# -- paths --------------------------------------------------------------------

# $0 is reliable when invoked as `bash path/to/script`; CDPATH must not hijack cd.
_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$_SCRIPT_DIR/.." && pwd)"
HOME_DIR="${CYCLAW_HOME:-$HOME/.CyClaw}"

reject_shell_metachars() {
  case "$1" in
    *'"'*|*'`'*|*'$'*|*'\'*)
      echo "cyclaw: refusing a path containing shell metacharacters (\", \`, \$, or \\): $1" >&2
      exit 1
      ;;
  esac
}

_looks_like_repo() {
  [ -f "$1/gate.py" ] && [ -f "$1/harness/server.py" ] && [ -f "$1/macos/install-cyclaw.sh" ]
}

if ! _looks_like_repo "$REPO_DIR"; then
  die "not a CyClaw checkout (expected gate.py + harness/server.py next to macos/). Run this from the clone, after git clone."
fi
reject_shell_metachars "$REPO_DIR"
reject_shell_metachars "$HOME_DIR"

DEFAULT_MODEL="qwen3.8:27b-mlx"
SMALL_DEFAULT="qwen2.5:7b"

_read_shipped_model() {
  local py candidate
  for candidate in "$HOME_DIR/venv/bin/python" python3.12 python3 python; do
    if { [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; }; then
      py="$candidate"
      if "$py" -c 'import yaml' >/dev/null 2>&1; then
        "$py" -c '
import yaml, sys
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
print(cfg.get("models", {}).get("local_llm", {}).get("model", "") or "")
' "$REPO_DIR/config.yaml" 2>/dev/null && return 0
      fi
    fi
  done
  # Fallback: first `model:` under the local_llm block. Never print a secret.
  awk '
    $0 ~ /^  local_llm:/ { inblk=1; next }
    inblk && $0 ~ /^  [a-z]/ && $0 !~ /^    / { exit }
    inblk && $0 ~ /^    model:/ {
      sub(/^    model:[[:space:]]*/, "")
      gsub(/["'\'']/, "")
      print
      exit
    }
  ' "$REPO_DIR/config.yaml" 2>/dev/null || true
}

if [ -z "$OLLAMA_MODEL" ]; then
  if [ "$SMALL_MODEL" -eq 1 ]; then
    OLLAMA_MODEL="$SMALL_DEFAULT"
  else
    OLLAMA_MODEL="$(_read_shipped_model)"
    [ -n "$OLLAMA_MODEL" ] || OLLAMA_MODEL="$DEFAULT_MODEL"
  fi
fi

# Ollama tags are name:tag or ns/name:tag. Reject anything that would be
# surprising as `ollama pull` argv or a later config.yaml paste.
case "$OLLAMA_MODEL" in
  ""|*[!A-Za-z0-9._:/-]*|-*|*" "*)
    die "refusing ollama tag '$OLLAMA_MODEL' (expected name:tag, e.g. qwen3.8:27b-mlx)"
    ;;
esac

# -- confirm helper -----------------------------------------------------------

_confirm() {
  local prompt="$1" default="${2:-y}" reply
  if [ "$SKIP_PROMPTS" -eq 1 ]; then
    [ "$default" = "y" ]
    return $?
  fi
  if [ "$default" = "y" ]; then
    printf '[cyclaw] %s [Y/n] ' "$prompt" >&2
  else
    printf '[cyclaw] %s [y/N] ' "$prompt" >&2
  fi
  IFS= read -r reply || true
  case "$reply" in
    "") [ "$default" = "y" ] ;;
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# -- plan banner --------------------------------------------------------------

echo ""
step "CyClaw Apple Silicon setup-from-clone"
step "repo     : $REPO_DIR"
step "home     : $HOME_DIR"
step "model    : $OLLAMA_MODEL"
step "terminal : http://127.0.0.1:8787  (RAG gateway / static/terminal.html)"
step "harness  : http://127.0.0.1:8790  (coding console / static/harness.html)"
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
  step "dry-run plan (no writes, no network):"
  echo "  1. cyclaw-advisor verify.sh (dep-file presence)"
  echo "  2. brew analytics off (if brew is on PATH)"
  echo "  3. require Python 3.12.x (offer brew install python@3.12 if missing)"
  if [ "$SKIP_INSTALL" -eq 0 ]; then
    echo "  4. bash macos/install-cyclaw.sh --repo-path <this checkout>"
  else
    echo "  4. skip installer"
  fi
  if [ "$SKIP_KEYS" -eq 0 ]; then
    echo "  5. bash macos/setup-cyclaw-keys.sh  (prompts: Telegram, Claude, Grok, GitHub)"
  else
    echo "  5. skip keys"
  fi
  echo "  6. optional gh auth status (never prints a token)"
  if [ "$SKIP_OLLAMA" -eq 0 ]; then
    echo "  7. Ollama check + optional pull of $OLLAMA_MODEL (source macos/ollama-mlx.env before serve)"
  else
    echo "  7. skip Ollama"
  fi
  if [ "$SKIP_INDEX" -eq 0 ]; then
    echo "  8. python -m retrieval.indexer"
  else
    echo "  8. skip index"
  fi
  if [ "$NO_START" -eq 1 ]; then
    echo "  9. skip servers (--no-start)"
  else
    echo "  9. bash macos/invoke-cyclaw.sh   (both servers; Ctrl+C stops them)"
  fi
  exit 0
fi

# -- 1. cyclaw-advisor verify -------------------------------------------------
# verify.sh only checks dep-file presence. Do NOT run bootstrap.sh here —
# that skill harness git-fetches origin/main.

if [ "$SKIP_ADVISOR" -eq 0 ] && [ -x "$REPO_DIR/.claude/skills/cyclaw-advisor/verify.sh" ]; then
  step "cyclaw-advisor: verifying checkout posture"
  ( CDPATH= cd -- "$REPO_DIR" && bash .claude/skills/cyclaw-advisor/verify.sh ) || \
    warn "cyclaw-advisor verify.sh reported a problem; continuing"
elif [ "$SKIP_ADVISOR" -eq 0 ]; then
  warn "cyclaw-advisor verify.sh not executable; skipping"
fi

# -- 2. Homebrew analytics (only if brew is already here) ---------------------

if command -v brew >/dev/null 2>&1; then
  # CyClaw never launches brew on the request path. Analytics is Homebrew's
  # own telemetry (setup-guide.md). Opt out once, per machine, if brew exists.
  export HOMEBREW_NO_ANALYTICS=1
  if brew analytics off >/dev/null 2>&1; then
    step "Homebrew analytics off (persistent)"
  else
    warn "could not run 'brew analytics off'; continuing"
  fi
fi

# -- 3. Python 3.12 -----------------------------------------------------------

find_python312() {
  local candidate ver major minor
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
      if [ -n "$ver" ]; then
        major="${ver%%.*}"
        minor="${ver#*.}"
        if [ "$major" -eq 3 ] 2>/dev/null && [ "$minor" -eq 12 ] 2>/dev/null; then
          echo "$candidate"
          return 0
        fi
      fi
    fi
  done
  return 1
}

PY_CMD=""
if ! PY_CMD="$(find_python312)"; then
  warn "Python 3.12.x not found on PATH."
  if [ "$SKIP_PROMPTS" -eq 1 ]; then
    die "Python 3.12.x is required. Install it (brew install python@3.12, or https://www.python.org/downloads/macos/) and re-run."
  fi
  if command -v brew >/dev/null 2>&1 && _confirm "Install Python 3.12 with Homebrew (brew install python@3.12)?" "y"; then
    step "installing python@3.12 via Homebrew"
    brew install python@3.12
    # Apple Silicon Homebrew prefix.
    if [ -x /opt/homebrew/bin/python3.12 ]; then
      export PATH="/opt/homebrew/bin:$PATH"
    fi
    if ! PY_CMD="$(find_python312)"; then
      die "python@3.12 installed but 3.12.x still not on PATH. Open a new tab and re-run."
    fi
  else
    echo "[cyclaw] Install Python 3.12, then re-run this script:" >&2
    echo "         brew install python@3.12" >&2
    echo "         or https://www.python.org/downloads/macos/" >&2
    exit 1
  fi
fi
step "Python 3.12: $PY_CMD ($("$PY_CMD" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"

# -- 4. install-cyclaw.sh -----------------------------------------------------

if [ "$SKIP_INSTALL" -eq 0 ]; then
  # Regular arrays are bash 3.2-safe (associative arrays are not). Quoted
  # expansion keeps a path-with-spaces as one argv token.

  INSTALL_ARGS=(--repo-path "$REPO_DIR")
  [ "$SKIP_PYTHON_DEPS" -eq 1 ] && INSTALL_ARGS+=(--skip-python-deps)
  [ "$NO_FSCONNECT" -eq 1 ] && INSTALL_ARGS+=(--no-fsconnect)
  [ "$NO_PROFILE" -eq 1 ] && INSTALL_ARGS+=(--no-profile-edit)
  [ "$NO_PATH" -eq 1 ] && INSTALL_ARGS+=(--no-path-edit)
  step "running macos/install-cyclaw.sh (venv + torch==2.13.0 + requirements)"
  bash "$REPO_DIR/macos/install-cyclaw.sh" "${INSTALL_ARGS[@]}"
else
  step "skipping installer (--skip-install)"
fi

VENV_PY="$HOME_DIR/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    VENV_PY="$REPO_DIR/.venv/bin/python"
    warn "no venv at $HOME_DIR/venv; using clone .venv"
  else
    VENV_PY="$PY_CMD"
    warn "no CyClaw venv found; falling back to $VENV_PY"
  fi
fi

# -- 5. API keys --------------------------------------------------------------

if [ "$SKIP_KEYS" -eq 0 ]; then
  KEY_ARGS=(--repo-path "$REPO_DIR")
  [ "$SKIP_PROMPTS" -eq 1 ] && KEY_ARGS+=(--skip-prompts --no-print-key)
  [ "$GROK_DUMMY" -eq 1 ] && KEY_ARGS+=(--grok-dummy)
  [ "$ROTATE_KEY" -eq 1 ] && KEY_ARGS+=(--rotate)
  [ "$NO_PROFILE" -eq 1 ] && KEY_ARGS+=(--no-profile-edit)
  step "running macos/setup-cyclaw-keys.sh"
  step "  will autogenerate CYCLAW_API_KEY (openssl rand -hex 20)"
  step "  will prompt for Telegram, Claude (ANTHROPIC_API_KEY), Grok, GitHub"
  step "  skip is allowed on every prompt; secrets never hit config.yaml or argv"
  bash "$REPO_DIR/macos/setup-cyclaw-keys.sh" "${KEY_ARGS[@]}"
else
  step "skipping keys (--skip-keys)"
fi

# Load keys into THIS process so the servers inherit them.
# The dotenv is chmod 600 and gitignored. Never print its contents.
# xtrace would dump every assignment — refuse rather than leak.
_dotenv_mode() {
  if [ "$(uname -s)" = "Darwin" ]; then
    stat -f %Lp "$1" 2>/dev/null || true
  else
    stat -c %a "$1" 2>/dev/null || true
  fi
}

_source_dotenv() {
  local f="$1"
  local mode=""
  [ -f "$f" ] || return 1
  mode="$(_dotenv_mode "$f")"
  case "$mode" in
    600|400) ;;
    *)
      # Name the file and the remedy. Without them the operator sees only a
      # mode number here and "CYCLAW_API_KEY not set" below, and the actual
      # cause -- a dotenv other local accounts can read -- goes unstated.
      echo "[cyclaw] warn : refusing to source $f (mode ${mode:-unknown}; want 600 or 400). Fix with: chmod 600 $f" >&2
      return 1
      ;;
  esac
  # shellcheck disable=SC1090
  set -a
  . "$f"
  set +a
}

case "$-" in
  *x*) die "refusing to source .env with xtrace on (would print secrets). Re-run without bash -x." ;;
esac
# Chained on the result, not `-f`: a refused HOME file must not shadow the repo copy.
_source_dotenv "$HOME_DIR/.env" || _source_dotenv "$REPO_DIR/.env" || true

# -- 6. gh auth (optional, never prints a token) ------------------------------

if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    step "gh: already authenticated (status ok; token not printed)"
  elif [ -n "${GH_TOKEN:-}" ] || [ -n "${GITHUB_TOKEN:-}" ]; then
    if [ "$SKIP_PROMPTS" -eq 0 ] && _confirm "Log gh(1) in from the token we just stored (stdin, never argv)?" "y"; then
      # Prefer GH_TOKEN. Pipe via stdin — never an argv token (cyclaw-advisor).
      if [ -n "${GH_TOKEN:-}" ]; then
        printf '%s\n' "$GH_TOKEN" | gh auth login --with-token
      else
        printf '%s\n' "$GITHUB_TOKEN" | gh auth login --with-token
      fi
      step "gh: logged in via --with-token (stdin)"
    else
      step "gh: token is stored; run 'gh auth login' yourself when ready"
    fi
  else
    step "gh: not logged in and no GH_TOKEN stored. Later: gh auth login"
  fi
else
  step "gh: not on PATH. Optional: brew install gh && gh auth login"
fi

# -- 7. Ollama ----------------------------------------------------------------

_ollama_up() {
  curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1
}

_ollama_has_model() {
  local tag="$1"
  command -v ollama >/dev/null 2>&1 || return 1
  ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fxq "$tag"
}

# Source macos/ollama-mlx.env (KEY=value, no secrets). No-op if the file is
# missing. Does not restart an already-running Ollama.app — those tunings
# only apply to `ollama serve` launched from this script.
_ollama_apply_mlx_env() {
  local envf="$REPO_DIR/macos/ollama-mlx.env"
  [ -f "$envf" ] || return 0
  set -a
  # shellcheck disable=SC1090
  . "$envf"
  set +a
}

if [ "$SKIP_OLLAMA" -eq 0 ]; then
  if command -v ollama >/dev/null 2>&1; then
    step "Ollama CLI present ($(ollama --version 2>/dev/null || echo unknown))"
  else
    warn "Ollama is not on PATH."
    if [ "$SKIP_PROMPTS" -eq 0 ]; then
      if [ "$OLLAMA_INSTALL_SCRIPT" -eq 1 ] && _confirm "Install Ollama via the unsigned ollama.com/install.sh pipe?" "n"; then
        # Explicit opt-in only. setup-guide.md prefers the signed .app.
        curl -fsSL https://ollama.com/install.sh | sh
      else
        step "opening the signed Ollama .app download page (preferred)"
        if command -v open >/dev/null 2>&1; then
          open "https://ollama.com/download/mac" || true
        fi
        echo "[cyclaw] Install the .app, launch it once, then re-run this script" >&2
        echo "         or continue and pull the model later: ollama pull $OLLAMA_MODEL" >&2
      fi
    fi
  fi

  if command -v ollama >/dev/null 2>&1; then
    if ! _ollama_up; then
      # The .app usually already serves :11434. A second `ollama serve` is
      # address-already-in-use — that is success, not a problem.
      step "Ollama API not answering yet; launching 'ollama serve' in the background"
      _ollama_apply_mlx_env
      ollama serve >/tmp/cyclaw-ollama-serve.log 2>&1 &
      sleep 1
      if _ollama_up; then
        step "Ollama API is up on 127.0.0.1:11434"
      else
        warn "Ollama still not answering /api/tags. If the .app is open, that is fine — wait a few seconds."
      fi
    else
      step "Ollama API already up on 127.0.0.1:11434"
    fi

    if _ollama_has_model "$OLLAMA_MODEL"; then
      step "Ollama already has $OLLAMA_MODEL"
    else
      # 27B is multi-GB. Never pull it under --skip-prompts without an
      # explicit --small-model / --ollama-model; ask in interactive mode.
      _do_pull=0
      if [ "$SKIP_PROMPTS" -eq 1 ]; then
        if [ "$SMALL_MODEL" -eq 1 ]; then
          _do_pull=1
        else
          step "skipping ollama pull of $OLLAMA_MODEL under --skip-prompts (multi-GB). Re-run without --skip-prompts, or pass --small-model."
        fi
      elif _confirm "Pull Ollama model '$OLLAMA_MODEL'? (qwen3.8:27b-mlx is multi-GB; pass --small-model for 7B)" "y"; then
        _do_pull=1
      fi
      if [ "$_do_pull" -eq 1 ]; then
        step "ollama pull $OLLAMA_MODEL"
        ollama pull "$OLLAMA_MODEL"
      fi
    fi
  fi
else
  step "skipping Ollama (--skip-ollama)"
fi

# If the operator pulled a tag other than the shipped default, do NOT edit
# config.yaml (cyclaw-advisor + config-guard C11). Warn so /query does not 404.
_SHIPPED_MODEL="$(_read_shipped_model)"
if [ -n "$_SHIPPED_MODEL" ] && [ "$OLLAMA_MODEL" != "$_SHIPPED_MODEL" ]; then
  warn "using Ollama tag $OLLAMA_MODEL but config.yaml ships $_SHIPPED_MODEL."
  warn "update BOTH models.local_llm.model and guardrails.model (config-guard C11) or /query will 404."
fi

# -- 8. retrieval index -------------------------------------------------------

if [ "$SKIP_INDEX" -eq 0 ]; then
  if [ -d "$REPO_DIR/index" ] && [ -n "$(ls -A "$REPO_DIR/index" 2>/dev/null || true)" ]; then
    step "retrieval index already present at $REPO_DIR/index"
  else
    step "building retrieval index (python -m retrieval.indexer)"
    ( CDPATH= cd -- "$REPO_DIR" && "$VENV_PY" -m retrieval.indexer )
    step "index built"
  fi
else
  step "skipping indexer (--skip-index). POST /query will 503 INDEX_NOT_FOUND until you run it."
fi

# -- 9. start both servers ----------------------------------------------------

_should_start=0
if [ "$NO_START" -eq 1 ]; then
  _should_start=0
elif [ "$FORCE_START" -eq 1 ]; then
  _should_start=1
elif [ "$SKIP_PROMPTS" -eq 1 ]; then
  _should_start=0
  step "not starting servers under --skip-prompts (pass --start to launch them)"
elif _confirm "Start both servers now (terminal :8787 + harness :8790)?" "y"; then
  _should_start=1
fi

echo ""
step "setup complete."
step "  terminal UI : http://127.0.0.1:8787"
step "  harness UI  : http://127.0.0.1:8790"
step "  this tab    : source $HOME_DIR/.env   (if you open a new one, rc already sources it)"
step "  later       : cyclaw     (or: bash macos/invoke-cyclaw.sh)"
step "  stop        : Ctrl+C in the tab that is running the servers"
if [ -z "${CYCLAW_API_KEY:-}" ]; then
  warn "CYCLAW_API_KEY is unset in this process — Soul / ops / harness state-changing routes will 401."
  warn "source $HOME_DIR/.env  then re-run  bash macos/invoke-cyclaw.sh"
fi
echo ""

if [ "$_should_start" -eq 1 ]; then
  INVOKE_ARGS=(--repo "$REPO_DIR")
  [ "$NO_BROWSER" -eq 1 ] && INVOKE_ARGS+=(--no-browser)
  step "starting RAG gateway + coding harness (Ctrl+C stops both)"
  # exec so Ctrl+C / the EXIT trap in invoke-cyclaw.sh own the process tree.
  exec bash "$REPO_DIR/macos/invoke-cyclaw.sh" "${INVOKE_ARGS[@]}"
fi

exit 0
