#!/usr/bin/env bash
# Bootstrap CyClaw API keys on macOS Apple Silicon.
#
# Autogenerates CYCLAW_API_KEY (openssl rand -hex 20 — the value setup-guide.md
# and README document) and prompts for the operator-supplied tokens CyClaw
# cannot invent: Telegram, Claude (ANTHROPIC_API_KEY — llm/client.py never
# reads CLAUDE_API_KEY), Grok, and GitHub. Skip is allowed on every prompt.
#
# Persistence (survives reboot; matches macos/ conventions + cyclaw-advisor):
#   1. macOS Keychain — the official path. launchd generators chain
#      cyclaw-keychain-env.sh; they never read .env and never write a token
#      into a plist or the cyclaw shim.
#   2. $CYCLAW_HOME/.env (default ~/.CyClaw/.env, chmod 600) — operator-requested
#      dotenv. Gitignored (*.env). Sourced by new shells via a marker block.
#   3. Optional checkout .env if a CyClaw repo is found (also gitignored).
#   4. rc file sources the same dotenv this script wrote. Secrets are NEVER
#      inlined into ~/.zshrc / ~/.bash_profile (install-cyclaw.sh already
#      refuses to put a secret in a profile file).
#
# Keychain store never puts a secret on `security`'s argv (same contract as
# cyclaw-keychain-set.sh): generated / typed values go through a 0600 temp
# file and /usr/bin/expect, or a TTY prompt. `ps` cannot see them.
#
# Usage:
#   bash macos/setup-cyclaw-keys.sh
#   bash macos/setup-cyclaw-keys.sh --skip-prompts --no-print-key
#   bash macos/setup-cyclaw-keys.sh --rotate
#   source ~/.CyClaw/.env          # load into THIS tab after a non-sourced run
#
# Options:
#   --rotate            replace an existing CYCLAW_API_KEY
#   --skip-prompts      no Telegram/Claude/Grok/GitHub prompts (autogen only)
#   --grok-dummy        set GROK_API_KEY=dummy (offline / pytest)
#   --no-keychain       skip Keychain (not recommended; launchd will 401)
#   --no-env-file       do not write ~/.CyClaw/.env
#   --no-repo-env       do not write a checkout .env even if a repo is found
#   --no-profile-edit   do not add the rc source block
#   --no-print-key      do not print the generated CYCLAW_API_KEY
#   --print-key         print it (default when a new key is generated)
#   --repo-path PATH    CyClaw checkout to receive a sibling .env
#   --help
#
# Target: macOS 14+ Apple Silicon (arm64), bash 3.2 / zsh, BSD userland.
# No Homebrew required. Tests set CYCLAW_SETUP_KEYS_SKIP_PLATFORM=1.
#
# Privacy (cyclaw-advisor): never log secret values, never write them to
# config.yaml, never put them in argv of a child we do not control. step()
# messages name services and variable names only.

set -euo pipefail

usage() {
  sed -n '3,42p' "$0" | sed 's/^# \{0,1\}//'
}

step() { printf '[cyclaw] %s\n' "$1"; }
warn() { printf '[cyclaw] WARNING: %s\n' "$1" >&2; }

# Documented Keychain service names (telegram + generate_service_plist.py)
# plus matching com.cgfixit.cyclaw.* names for the other env vars.
KC_API="com.cgfixit.cyclaw.api-key"
KC_TELEGRAM="com.cgfixit.cyclaw.telegram-bot-token"
KC_GROK="com.cgfixit.cyclaw.grok-api-key"
KC_ANTHROPIC="com.cgfixit.cyclaw.anthropic-api-key"
KC_GH="com.cgfixit.cyclaw.gh-token"

ROTATE=0
SKIP_PROMPTS=0
GROK_DUMMY=0
DO_KEYCHAIN=1
DO_ENV_FILE=1
DO_REPO_ENV=1
DO_PROFILE=1
PRINT_KEY="auto"
REPO_PATH=""

while [ $# -gt 0 ]; do
  case "$1" in
    --rotate) ROTATE=1; shift ;;
    --skip-prompts|--yes) SKIP_PROMPTS=1; shift ;;
    --grok-dummy) GROK_DUMMY=1; shift ;;
    --no-keychain) DO_KEYCHAIN=0; shift ;;
    --no-env-file) DO_ENV_FILE=0; shift ;;
    --no-repo-env) DO_REPO_ENV=0; shift ;;
    --no-profile-edit) DO_PROFILE=0; shift ;;
    --no-print-key) PRINT_KEY="never"; shift ;;
    --print-key) PRINT_KEY="always"; shift ;;
    --repo-path) REPO_PATH="${2:?--repo-path requires a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# -- platform -----------------------------------------------------------------

_require_platform() {
  if [ "${CYCLAW_SETUP_KEYS_SKIP_PLATFORM:-}" = "1" ]; then
    return 0
  fi
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "[cyclaw] this script is for macOS Apple Silicon only (uname -s is $(uname -s))." >&2
    exit 1
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
    warn "macOS 14 Sonoma is the CyClaw floor (detected ${ver}). Continuing anyway."
  fi
}

_require_platform

if [ "$SKIP_PROMPTS" -eq 0 ] && [ ! -t 0 ]; then
  echo "[cyclaw] interactive prompts need a TTY. Re-run in a terminal, or pass --skip-prompts." >&2
  exit 1
fi

# -- paths --------------------------------------------------------------------

# $0 is reliable when invoked as `bash path/to/script`; CDPATH must not
# hijack `cd` (same class of footgun install-cyclaw.sh documents).
_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
HOME_DIR="${CYCLAW_HOME:-$HOME/.CyClaw}"
ACCOUNT="$(id -un)"

reject_shell_metachars() {
  case "$1" in
    *'"'*|*\`*|*'$'*|*\\*)
      echo "cyclaw: refusing a path containing shell metacharacters (\", \`, \$, or \\): $1" >&2
      exit 1
      ;;
  esac
}

_looks_like_repo() {
  [ -f "$1/gate.py" ] || [ -f "$1/harness/server.py" ]
}

REPO_DIR=""
_find_repo() {
  local cand
  if [ -n "$REPO_PATH" ]; then
    if ! _looks_like_repo "$REPO_PATH"; then
      echo "--repo-path '$REPO_PATH' does not look like a CyClaw checkout (missing gate.py / harness/server.py)." >&2
      exit 1
    fi
    REPO_DIR="$(CDPATH= cd -- "$REPO_PATH" && pwd)"
    reject_shell_metachars "$REPO_DIR"
    return 0
  fi
  for cand in \
    "${CYCLAW_REPO:-}" \
    "$PWD" \
    "$_SCRIPT_DIR/.." \
    "$HOME_DIR/repo"
  do
    [ -n "$cand" ] || continue
    [ -d "$cand" ] || continue
    if _looks_like_repo "$cand"; then
      REPO_DIR="$(CDPATH= cd -- "$cand" && pwd)"
      reject_shell_metachars "$REPO_DIR"
      return 0
    fi
  done
  return 0
}

_find_repo

mkdir -p "$HOME_DIR"
chmod 700 "$HOME_DIR" 2>/dev/null || true
HOME_DIR="$(CDPATH= cd -- "$HOME_DIR" && pwd)"
reject_shell_metachars "$HOME_DIR"
ENV_FILE="$HOME_DIR/.env"

# -- quoting / dotenv ---------------------------------------------------------

# Single-quote for a POSIX assignment. 'foo'bar' -> 'foo'\''bar'
_shell_single_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

_validate_secret() {
  case "$1" in
    "") return 1 ;;
    *$'\n'*|*$'\r'*) return 1 ;;
  esac
  return 0
}

# Rewrite one KEY= assignment in a dotenv file. Never prints the value.
# Accepts both `KEY=` and `export KEY=` forms on the way in; writes `export`.
_env_upsert() {
  local file="$1" key="$2" value="$3" tmp quoted old_umask
  if ! _validate_secret "$value"; then
    echo "[cyclaw] refusing to store an empty or multi-line value for $key" >&2
    return 1
  fi
  quoted="$(_shell_single_quote "$value")"
  old_umask="$(umask)"
  umask 077
  tmp="$(mktemp "${TMPDIR:-/tmp}/cyclaw.env.XXXXXX")"
  if [ -f "$file" ]; then
    # grep -v returns 1 when every line matches (empty result) — do not trip set -e.
    grep -v -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" > "$tmp" || true
  else
    printf '%s\n' "# CyClaw secrets — chmod 600. Managed by macos/setup-cyclaw-keys.sh." > "$tmp"
    printf '%s\n' "# Do not commit. Do not copy into config.yaml. Do not paste into chat logs." >> "$tmp"
  fi
  printf 'export %s=%s\n' "$key" "$quoted" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$file"
  chmod 600 "$file"
  umask "$old_umask"
}

_env_has() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 1
  grep -E -q "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null
}

# Inverse of _shell_single_quote. Also accepts a bare token or one pair of
# double quotes (we never write those, but a hand-edited .env might have them).
_env_unquote() {
  local raw="$1" inner
  case "$raw" in
    \'*\')
      inner="${raw#\'}"
      inner="${inner%\'}"
      # Remaining '\'' is the encoding _shell_single_quote writes for `'`.
      printf '%s' "$inner" | sed "s/'\\\\''/'/g"
      ;;
    \"*\")
      inner="${raw#\"}"
      inner="${inner%\"}"
      printf '%s' "$inner"
      ;;
    *)
      printf '%s' "$raw"
      ;;
  esac
}

# Extract a KEY's value from a dotenv we wrote (export KEY='...').
# Only used to sync an existing .env into Keychain. Never echoed.
_env_get() {
  local file="$1" key="$2" line
  [ -f "$file" ] || return 1
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" | tail -n 1)" || return 1
  line="${line#*=}"
  _env_unquote "$line"
}

# -- Keychain -----------------------------------------------------------------

_keychain_get() {
  security find-generic-password -a "$ACCOUNT" -s "$1" -w 2>/dev/null || true
}

_keychain_has() {
  local val
  val="$(_keychain_get "$1")"
  [ -n "$val" ]
}

# Store the contents of a 0600 file as a generic password. The secret is
# never an argv token of `security` (bare `-w`, same as cyclaw-keychain-set.sh).
_keychain_store_file() {
  local service="$1" secret_file="$2"
  if [ ! -f "$secret_file" ]; then
    echo "[cyclaw] internal: missing secret file for $service" >&2
    return 1
  fi

  # Test / CI path: the fake `security` stub reads stdin after a bare -w.
  if [ "${CYCLAW_SETUP_KEYS_STDIN_STORE:-}" = "1" ]; then
    security add-generic-password -a "$ACCOUNT" -s "$service" -T /usr/bin/security -U -w < "$secret_file"
    return $?
  fi

  if [ -x /usr/bin/expect ]; then
    # expect reads the secret from the file (not its own argv) and types it
    # into security's TTY prompt. log_user 0 keeps it off the transcript.
    /usr/bin/expect <<EXPECT_EOF
set timeout 30
set fh [open "$secret_file" r]
set secret [read -nonewline \$fh]
close \$fh
log_user 0
spawn -noecho /usr/bin/security add-generic-password -a "$ACCOUNT" -s "$service" -T /usr/bin/security -U -w
expect {
  -re {(?i)password} { send -- "\$secret\r"; exp_continue }
  eof
}
catch wait result
exit [lindex \$result 3]
EXPECT_EOF
    return $?
  fi

  if [ -x "$_SCRIPT_DIR/cyclaw-keychain-set.sh" ] && [ -t 0 ]; then
    warn "expect not found; falling back to interactive Keychain prompt for $service"
    "$_SCRIPT_DIR/cyclaw-keychain-set.sh" "$service"
    return $?
  fi

  warn "could not store service=$service in Keychain (no expect, no TTY). .env still written."
  return 0
}

_keychain_store_value() {
  local service="$1" value="$2" tmp rc old_umask
  old_umask="$(umask)"
  umask 077
  tmp="$(mktemp "${TMPDIR:-/tmp}/cyclaw.kc.XXXXXX")"
  printf '%s' "$value" > "$tmp"
  chmod 600 "$tmp"
  umask "$old_umask"
  rc=0
  _keychain_store_file "$service" "$tmp" || rc=$?
  rm -f "$tmp"
  return "$rc"
}

# -- persist one secret -------------------------------------------------------

# Tracks names we actually wrote (never values) for the closing summary.
_STORED_NAMES=""
_NOTE() { _STORED_NAMES="${_STORED_NAMES}${_STORED_NAMES:+ }$1"; }

_persist() {
  local env_name="$1" service="$2" value="$3" extra="${4:-}"
  if ! _validate_secret "$value"; then
    echo "[cyclaw] refusing empty/multi-line value for $env_name" >&2
    return 1
  fi

  if [ "$DO_KEYCHAIN" -eq 1 ]; then
    if command -v security >/dev/null 2>&1; then
      if _keychain_store_value "$service" "$value"; then
        step "Keychain: stored $env_name (service=$service account=$ACCOUNT)"
      else
        warn "Keychain store failed for $env_name (service=$service)"
      fi
    else
      warn "security(1) not on PATH — skipped Keychain for $env_name"
    fi
  fi

  if [ "$DO_ENV_FILE" -eq 1 ]; then
    _env_upsert "$ENV_FILE" "$env_name" "$value"
    if [ -n "$extra" ]; then
      _env_upsert "$ENV_FILE" "$extra" "$value"
    fi
    step "wrote $env_name to $ENV_FILE (mode 600)"
  fi

  if [ "$DO_REPO_ENV" -eq 1 ] && [ -n "$REPO_DIR" ]; then
    _env_upsert "$REPO_DIR/.env" "$env_name" "$value"
    if [ -n "$extra" ]; then
      _env_upsert "$REPO_DIR/.env" "$extra" "$value"
    fi
    step "wrote $env_name to $REPO_DIR/.env (gitignored, mode 600)"
  fi

  # Current process only. A non-sourced run cannot export into the parent
  # tab — the operator sources ~/.CyClaw/.env (or opens a new shell).
  export "${env_name}=${value}"
  if [ -n "$extra" ]; then
    export "${extra}=${value}"
  fi
  _NOTE "$env_name"
}

# -- rc source block (no secrets) ---------------------------------------------

KEYS_START="# >>> cyclaw keys >>>"
KEYS_END="# <<< cyclaw keys <<<"

detect_rc_file() {
  case "${SHELL:-}" in
    */zsh) echo "$HOME/.zshrc" ;;
    *)
      if [ "$(uname -s)" = "Darwin" ]; then
        for candidate in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
          if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
          fi
        done
        echo "$HOME/.bash_profile"
      elif [ -f "$HOME/.bash_profile" ]; then echo "$HOME/.bash_profile"
      else echo "$HOME/.bashrc"
      fi
      ;;
  esac
}

# Print the dotenv source lines (no markers). Default home stays portable
# (`$HOME/.CyClaw/.env`); a custom CYCLAW_HOME is single-quoted absolutely.
_rc_source_body() {
  local quoted
  if [ "$HOME_DIR" = "$HOME/.CyClaw" ]; then
    echo "# Loads \$HOME/.CyClaw/.env (chmod 600). Secrets are not stored in this rc file."
    echo "if [ -f \"\$HOME/.CyClaw/.env\" ]; then"
    echo "  . \"\$HOME/.CyClaw/.env\""
    echo "fi"
  else
    quoted="$(_shell_single_quote "$ENV_FILE")"
    echo "# Loads the CYCLAW_HOME dotenv (chmod 600). Secrets are not stored in this rc file."
    echo "if [ -f $quoted ]; then"
    echo "  . $quoted"
    echo "fi"
  fi
}

_ensure_rc_source() {
  local rc_file
  [ "$DO_PROFILE" -eq 1 ] || return 0
  rc_file="$(detect_rc_file)"
  [ -f "$rc_file" ] || touch "$rc_file"

  # Fail closed on a half-written marker block: never append a second copy
  # and never silently treat a corrupt block as "already installed."
  if grep -qxF "$KEYS_START" "$rc_file" 2>/dev/null; then
    if ! grep -qxF "$KEYS_END" "$rc_file" 2>/dev/null; then
      echo "[cyclaw] malformed cyclaw keys block in $rc_file (start without end); left unchanged" >&2
      return 1
    fi
    step "rc already sources the keys dotenv ($rc_file)"
    return 0
  fi
  if grep -qxF "$KEYS_END" "$rc_file" 2>/dev/null; then
    echo "[cyclaw] malformed cyclaw keys block in $rc_file (end without start); left unchanged" >&2
    return 1
  fi

  {
    echo ""
    echo "$KEYS_START"
    _rc_source_body
    echo "$KEYS_END"
  } >> "$rc_file"
  step "added $ENV_FILE source block to $rc_file (new shells inherit keys)"
}

# -- prompts ------------------------------------------------------------------

# Read a secret with no echo. Empty / "skip" / "s" / "n" means skip.
# Existing presence is announced without revealing the value.
_prompt_secret() {
  local label="$1" env_name="$2" service="$3" outvar="$4" typed="" present=""
  if [ "$SKIP_PROMPTS" -eq 1 ]; then
    eval "$outvar=''"
    return 0
  fi
  if _keychain_has "$service" 2>/dev/null; then
    present="Keychain"
  elif _env_has "$ENV_FILE" "$env_name"; then
    present=".env"
  elif [ -n "$(eval "printf '%s' \"\${$env_name:-}\"")" ]; then
    present="environment"
  fi
  if [ -n "$present" ]; then
    printf '[cyclaw] %s (%s) already set via %s. Enter a new value to replace, or press return to keep: ' \
      "$label" "$env_name" "$present" >&2
  else
    printf '[cyclaw] %s (%s) — paste the secret, or press return to skip: ' \
      "$label" "$env_name" >&2
  fi
  # -s: no echo. bash 3.2 supports it. Restore echo on any exit path.
  # INT/TERM must restore echo AND abort (130 / 143). A restore-only trap
  # plus `read || true` would treat Ctrl-C as "skip" and keep writing state.
  _restore_tty_echo() { stty echo 2>/dev/null || true; }
  _prompt_on_int() { _restore_tty_echo; printf '\n' >&2; exit 130; }
  _prompt_on_term() { _restore_tty_echo; printf '\n' >&2; exit 143; }
  trap '_restore_tty_echo' EXIT
  trap '_prompt_on_int' INT
  trap '_prompt_on_term' TERM
  stty -echo 2>/dev/null || true
  IFS= read -r typed || true
  stty echo 2>/dev/null || true
  trap - EXIT INT TERM
  printf '\n' >&2
  case "$typed" in
    ""|skip|SKIP|s|S|n|N)
      eval "$outvar=''"
      return 0
      ;;
  esac
  if ! _validate_secret "$typed"; then
    warn "$env_name rejected (empty or contained a newline) — skipped"
    eval "$outvar=''"
    return 0
  fi
  eval "$outvar=\$typed"
}

# -- CYCLAW_API_KEY (autogenerate) --------------------------------------------

_api_source=""
_api_value=""

if [ "$ROTATE" -eq 0 ]; then
  if _keychain_has "$KC_API" 2>/dev/null; then
    _api_value="$(_keychain_get "$KC_API")"
    _api_source="Keychain"
  elif _env_has "$ENV_FILE" "CYCLAW_API_KEY"; then
    _api_value="$(_env_get "$ENV_FILE" "CYCLAW_API_KEY")"
    _api_source=".env"
  elif [ -n "${CYCLAW_API_KEY:-}" ]; then
    _api_value="$CYCLAW_API_KEY"
    _api_source="environment"
  fi
fi

if [ -n "$_api_value" ] && [ "$ROTATE" -eq 0 ]; then
  step "CYCLAW_API_KEY already present ($_api_source) — keeping (pass --rotate to replace)"
  _persist "CYCLAW_API_KEY" "$KC_API" "$_api_value"
  GENERATED_NEW=0
else
  if ! command -v openssl >/dev/null 2>&1; then
    echo "[cyclaw] openssl is required to generate CYCLAW_API_KEY (it ships with macOS)." >&2
    exit 1
  fi
  _api_value="$(openssl rand -hex 20)"
  if ! _validate_secret "$_api_value"; then
    echo "[cyclaw] openssl produced an unusable CYCLAW_API_KEY" >&2
    exit 1
  fi
  step "generated CYCLAW_API_KEY (openssl rand -hex 20)"
  _persist "CYCLAW_API_KEY" "$KC_API" "$_api_value"
  GENERATED_NEW=1
fi

# -- optional operator tokens -------------------------------------------------

TELEGRAM_VAL=""
ANTHROPIC_VAL=""
GROK_VAL=""
GH_VAL=""

_prompt_secret "Telegram bot token" "TELEGRAM_BOT_TOKEN" "$KC_TELEGRAM" TELEGRAM_VAL
if [ -n "$TELEGRAM_VAL" ]; then
  _persist "TELEGRAM_BOT_TOKEN" "$KC_TELEGRAM" "$TELEGRAM_VAL"
else
  step "Telegram: skipped"
fi

_prompt_secret "Claude / Anthropic API key" "ANTHROPIC_API_KEY" "$KC_ANTHROPIC" ANTHROPIC_VAL
if [ -n "$ANTHROPIC_VAL" ]; then
  _persist "ANTHROPIC_API_KEY" "$KC_ANTHROPIC" "$ANTHROPIC_VAL"
else
  step "Claude (ANTHROPIC_API_KEY): skipped — llm/client.py reads this name, not CLAUDE_API_KEY"
fi

if [ "$GROK_DUMMY" -eq 1 ]; then
  GROK_VAL="dummy"
  step "GROK_API_KEY=dummy (--grok-dummy; fine for offline / pytest)"
  _persist "GROK_API_KEY" "$KC_GROK" "$GROK_VAL"
else
  _prompt_secret "Grok / xAI API key (type dummy for offline tests)" "GROK_API_KEY" "$KC_GROK" GROK_VAL
  if [ -n "$GROK_VAL" ]; then
    _persist "GROK_API_KEY" "$KC_GROK" "$GROK_VAL"
  else
    step "Grok: skipped — pytest needs any non-empty GROK_API_KEY (try --grok-dummy)"
  fi
fi

_prompt_secret "GitHub token (ghp_ / github_pat_)" "GH_TOKEN" "$KC_GH" GH_VAL
if [ -n "$GH_VAL" ]; then
  # CyClaw agentic reads GH_TOKEN; some gh(1) versions also honour GITHUB_TOKEN.
  _persist "GH_TOKEN" "$KC_GH" "$GH_VAL" "GITHUB_TOKEN"
else
  step "GitHub: skipped — gh stays logged in via its own keyring if you already ran gh auth login"
fi

_ensure_rc_source

# -- summary (names and paths only) -------------------------------------------

echo ""
step "done. stored: ${_STORED_NAMES:-nothing new}"
step "home dotenv : $ENV_FILE"
if [ -n "$REPO_DIR" ]; then
  step "repo dotenv : $REPO_DIR/.env"
else
  step "repo dotenv : (no CyClaw checkout found; pass --repo-path to write one)"
fi
step "this tab    : source $ENV_FILE"
step "new tabs    : inherit via the rc source block after you open them"
step "launchd     : still uses Keychain via cyclaw-keychain-env.sh — never .env"

if [ "$GENERATED_NEW" -eq 1 ] && [ "$PRINT_KEY" != "never" ]; then
  echo ""
  step "CYCLAW_API_KEY (copy once; paste into the Soul / operator console):"
  printf '%s\n' "$_api_value"
elif [ "$PRINT_KEY" = "always" ]; then
  echo ""
  step "CYCLAW_API_KEY (copy once; paste into the Soul / operator console):"
  printf '%s\n' "$_api_value"
fi

# Drop locals that held operator-typed secrets. The exported env vars stay.
TELEGRAM_VAL=""
ANTHROPIC_VAL=""
GROK_VAL=""
GH_VAL=""
unset TELEGRAM_VAL ANTHROPIC_VAL GROK_VAL GH_VAL
