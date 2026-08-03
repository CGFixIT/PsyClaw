#!/usr/bin/env bash
# CyClaw coding harness installer -- macOS (Apple Silicon arm64) and Linux.
#
# Mirrors powershell/Install-CyClaw.ps1's behavior and home layout exactly
# (see that file's header for the full home-directory description). This is a
# separate OS-native script, not a shared abstraction with the PowerShell
# scripts -- there is no meaningful capability overlap between a .ps1 file and
# a POSIX shell script beyond both eventually running `python -m harness.server`.
#
# After install, typing `cyclaw` in any new terminal starts the grok-build-style
# local coding harness (browser console on 127.0.0.1:8790).
#
# Everything mutable lives under ~/.CyClaw:
#   repo/       the CyClaw checkout (cloned, or linked via --repo-path)
#   venv/       the Python virtual environment
#   bin/        the cyclaw shim + launcher
#   sessions/   chat sessions with token tallies
#   skills/     user-visible copy of .claude/skills
#   tools/      connector/tool state
#   memory/     harness memory log
#   config.json selected model, soul on/off
#
# Usage:
#   bash macos/install-cyclaw.sh
#   bash macos/install-cyclaw.sh --repo-path ~/src/CyClaw
#
# Options:
#   --repo-path PATH     use an existing CyClaw clone instead of cloning
#   --skip-python-deps   create the home layout + shim but skip venv/pip installs
#                        (use when deps are already installed in an env you'll point to)
#   --no-profile-edit    do not add the cyclaw() function to the shell rc file
#                        (the PATH entry is still added unless --no-path-edit)
#   --no-path-edit       do not modify PATH via the shell rc file
#
# Target shells: bash (including macOS's stock 3.2) and zsh. BSD userland
# assumed on macOS -- no GNU-only flags, no Homebrew dependency declared or
# required (Homebrew Python works fine if present, but is never assumed).

set -euo pipefail

REPO_URL="https://github.com/CGFixIT/CyClaw.git"
REPO_PATH=""
SKIP_PYTHON_DEPS=0
NO_PROFILE_EDIT=0
NO_PATH_EDIT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?--repo-path requires a value}"; shift 2 ;;
    --skip-python-deps) SKIP_PYTHON_DEPS=1; shift ;;
    --no-profile-edit) NO_PROFILE_EDIT=1; shift ;;
    --no-path-edit) NO_PATH_EDIT=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

step() { printf '[cyclaw] %s\n' "$1"; }
warn() { printf '[cyclaw] WARNING: %s\n' "$1" >&2; }

# A repo path (the default $HOME_DIR/repo, or an operator-supplied
# --repo-path once canonicalized) gets interpolated, unescaped, into two
# generated files below: the cyclaw shim (a chmod +x'd script) and this rc
# file's cyclaw() function, both inside a double-quoted
# CYCLAW_REPO="..." assignment. Bash double-quoting suppresses word-splitting
# and globbing but NOT command substitution -- a literal embedded $(...) or
# `...` in the path is evaluated for real the next time the generated file is
# parsed and run (confirmed empirically: a value containing $(touch pwned)
# with no quote character at all still executes on re-parse), and a literal
# embedded " closes the quoted region early, turning everything after it into
# live shell syntax too. POSIX filenames may legally contain any of these
# (only / and NUL are forbidden), so this is a real, constructible primitive
# from an operator-controlled --repo-path, not a theoretical one. Reject
# outright rather than trying to correctly escape at every write site.
reject_shell_metachars() {
  case "$1" in
    *'"'*|*'`'*|*'$'*|*'\'*)
      echo "cyclaw: refusing a repo path containing shell metacharacters (\", \`, \$, or \\): $1" >&2
      exit 1
      ;;
  esac
}

# -- 1. Home layout ------------------------------------------------------------
HOME_DIR="$HOME/.CyClaw"
BIN_DIR="$HOME_DIR/bin"
REPO_DIR="$HOME_DIR/repo"
VENV_DIR="$HOME_DIR/venv"
for d in "$HOME_DIR" "$BIN_DIR" "$HOME_DIR/sessions" "$HOME_DIR/skills" "$HOME_DIR/tools" "$HOME_DIR/memory"; do
  [ -d "$d" ] || mkdir -p "$d"
done
step "home layout ready at $HOME_DIR"

# -- 2. Repo --------------------------------------------------------------------
if [ -n "$REPO_PATH" ]; then
  if [ ! -f "$REPO_PATH/harness/server.py" ]; then
    echo "--repo-path '$REPO_PATH' does not look like a CyClaw checkout with the harness package." >&2
    exit 1
  fi
  REPO_DIR="$(cd "$REPO_PATH" && pwd)"
  step "using existing repo at $REPO_DIR"
elif [ ! -f "$REPO_DIR/harness/server.py" ]; then
  [ -d "$REPO_DIR" ] && rm -rf "$REPO_DIR"
  step "cloning CyClaw origin main to $REPO_DIR"
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
else
  step "repo already present at $REPO_DIR (pulling latest main)"
  git -C "$REPO_DIR" pull --ff-only
fi
reject_shell_metachars "$REPO_DIR"

# -- 3. Python + dependencies -----------------------------------------------------
find_python312() {
  local candidate ver major minor
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
      if [ -n "$ver" ]; then
        major="${ver%%.*}"
        minor="${ver#*.}"
        if [ "$major" -eq 3 ] 2>/dev/null && [ "$minor" -ge 12 ] 2>/dev/null; then
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
  warn "Python 3.12+ not found. Install it (e.g. 'brew install python@3.12', or https://www.python.org/downloads/macos/), then re-run this installer."
  if [ "$SKIP_PYTHON_DEPS" -eq 0 ]; then
    echo "Python 3.12+ is required." >&2
    exit 1
  fi
fi

if [ "$SKIP_PYTHON_DEPS" -eq 0 ]; then
  VENV_PY="$VENV_DIR/bin/python"
  if [ ! -x "$VENV_PY" ]; then
    step "creating virtual environment at $VENV_DIR"
    "$PY_CMD" -m venv "$VENV_DIR"
    [ -x "$VENV_PY" ] || { echo "venv creation failed." >&2; exit 1; }
  fi
  step "installing dependencies (torch first, then requirements; this can take a few minutes)"
  # Match ci.yml's exact pip pin (CVE/repro); never float to latest on installers.
  "$VENV_PY" -m pip install --upgrade "pip==26.1.2" >/dev/null
  if [ "$(uname -s)" = "Darwin" ]; then
    # Apple Silicon has no separate CPU/CUDA torch build to disambiguate, so
    # (unlike Linux) PyTorch publishes a PLAIN torch==2.13.0 for macOS --
    # confirmed against download.pytorch.org/whl/cpu's own version listing,
    # which 404s on the "+cpu"-suffixed pin requirements.txt/constraints.txt
    # hardcode for Linux/Windows reproducibility. Install torch directly, then
    # strip the torch/extra-index-url lines from copies of both manifests so
    # pip never tries to reconcile the installed plain build against that pin.
    "$VENV_PY" -m pip install "torch==2.13.0"
    TMP_REQ="$(mktemp)"
    TMP_CONSTRAINTS="$(mktemp)"
    grep -v -e '^torch==' -e '^--extra-index-url https://download.pytorch.org' "$REPO_DIR/requirements.txt" > "$TMP_REQ"
    grep -v '^torch==' "$REPO_DIR/constraints.txt" > "$TMP_CONSTRAINTS"
    "$VENV_PY" -m pip install -r "$TMP_REQ" -c "$TMP_CONSTRAINTS" --ignore-installed PyYAML
    rm -f "$TMP_REQ" "$TMP_CONSTRAINTS"
  else
    # Linux: requirements.txt already carries the correct
    # --extra-index-url/torch==2.13.0+cpu pair (unlike macOS, which has no
    # separate CPU/CUDA build to disambiguate -- see the Darwin branch above),
    # so no manifest-stripping workaround is needed here. A bare `pip install
    # torch==2.13.0` with no index override -- what this script did
    # unconditionally before this branch existed -- resolves PyPI's default
    # CUDA-bundled build instead of the pinned CPU-only one, silently
    # discarding the +cpu pin's reproducibility/security guarantee
    # (constraints.txt: pinned post-CVE-2025-32434). Matches CLAUDE.md's
    # documented two-step install order exactly.
    "$VENV_PY" -m pip install "torch==2.13.0+cpu" --index-url https://download.pytorch.org/whl/cpu
    "$VENV_PY" -m pip install -r "$REPO_DIR/requirements.txt" -c "$REPO_DIR/constraints.txt" --ignore-installed PyYAML
  fi
  step "dependencies installed"
fi

# -- 4. Launcher + shim -----------------------------------------------------------
cp "$REPO_DIR/macos/invoke-cyclaw.sh" "$BIN_DIR/invoke-cyclaw.sh"
chmod +x "$BIN_DIR/invoke-cyclaw.sh"

SHIM="$BIN_DIR/cyclaw"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
# CyClaw harness launcher (installed shim).
export CYCLAW_HOME="\$HOME/.CyClaw"
export CYCLAW_REPO="$REPO_DIR"
exec "\$CYCLAW_HOME/bin/invoke-cyclaw.sh" "\$@"
EOF
chmod +x "$SHIM"
step "launcher shim written to $SHIM"

# -- 5 & 6. PATH + shell rc function ----------------------------------------------
# macOS/Linux have no persistent user-level PATH store analogous to Windows'
# registry -- both the PATH export and the cyclaw() function are added to the
# same shell rc file, each behind its own marker block, so either can be
# independently skipped here (--no-path-edit / --no-profile-edit) or later
# removed by uninstall-cyclaw.sh without disturbing the other.
detect_rc_file() {
  case "${SHELL:-}" in
    */zsh) echo "$HOME/.zshrc" ;;
    *)
      if [ "$(uname -s)" = "Darwin" ]; then
        # Bash login-file precedence: do not create ~/.bash_profile over an
        # existing ~/.bash_login or ~/.profile and suppress the user's setup.
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
RC_FILE="$(detect_rc_file)"
[ -f "$RC_FILE" ] || touch "$RC_FILE"

if [ "$NO_PATH_EDIT" -eq 0 ]; then
  PATH_MARKER="# >>> cyclaw harness path >>>"
  if ! grep -qF "$PATH_MARKER" "$RC_FILE" 2>/dev/null; then
    {
      echo ""
      echo "$PATH_MARKER"
      echo "export PATH=\"$BIN_DIR:\$PATH\""
      echo "# <<< cyclaw harness path <<<"
    } >> "$RC_FILE"
    export PATH="$BIN_DIR:$PATH"
    step "added $BIN_DIR to PATH in $RC_FILE (new shells inherit it)"
  fi
fi

if [ "$NO_PROFILE_EDIT" -eq 0 ]; then
  FUNC_MARKER="# >>> cyclaw harness >>>"
  if ! grep -qF "$FUNC_MARKER" "$RC_FILE" 2>/dev/null; then
    {
      echo ""
      echo "$FUNC_MARKER"
      echo "cyclaw() {"
      echo "  CYCLAW_HOME=\"\$HOME/.CyClaw\" CYCLAW_REPO=\"$REPO_DIR\" \"\$HOME/.CyClaw/bin/invoke-cyclaw.sh\" \"\$@\""
      echo "}"
      echo "# <<< cyclaw harness <<<"
    } >> "$RC_FILE"
    step "added 'cyclaw' function to $RC_FILE"
  fi
fi

echo ""
step "install complete. Open a NEW terminal (or 'source $RC_FILE') and run:  cyclaw"
step "the harness console opens at http://127.0.0.1:8790 -- /help lists commands."
