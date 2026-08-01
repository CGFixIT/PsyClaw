#!/usr/bin/env bash
# Launches the CyClaw coding harness (installed by install-cyclaw.sh).
#
# macOS (Apple Silicon arm64) and Linux, bash or zsh. Starts the harness
# control plane on 127.0.0.1:8790 (loopback only) using the per-user venv
# under ~/.CyClaw/venv and the repo at $CYCLAW_REPO (or ~/.CyClaw/repo), then
# opens the console in the default browser. Ctrl+C stops the server.
#
# Usage:
#   cyclaw                                      # via the installed shim / rc function
#   bash macos/invoke-cyclaw.sh --no-browser --port 8800
#
# Options:
#   --port PORT   override the console port (default 8790; gate.py owns 8787)
#   --no-browser  do not open the browser; just serve
#   --repo PATH   explicit path to the CyClaw checkout (overrides $CYCLAW_REPO)

set -euo pipefail

PORT="${CYCLAW_HARNESS_PORT:-8790}"
NO_BROWSER=0
REPO_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port requires a value}"; shift 2 ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --repo) REPO_OVERRIDE="${2:?--repo requires a value}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

HOME_DIR="${CYCLAW_HOME:-$HOME/.CyClaw}"
if [ -n "$REPO_OVERRIDE" ]; then
  REPO_DIR="$REPO_OVERRIDE"
else
  REPO_DIR="${CYCLAW_REPO:-$HOME_DIR/repo}"
fi
VENV_PY="$HOME_DIR/venv/bin/python"

if [ ! -f "$REPO_DIR/harness/server.py" ]; then
  echo "CyClaw repo not found at '$REPO_DIR'. Run install-cyclaw.sh first (or pass --repo)." >&2
  exit 1
fi
if [ ! -x "$VENV_PY" ]; then
  # Fall back to system python3 when the venv was skipped during install.
  VENV_PY="$(command -v python3 || true)"
  if [ -z "$VENV_PY" ]; then
    echo "No venv at $HOME_DIR/venv and no python3 on PATH. Re-run install-cyclaw.sh." >&2
    exit 1
  fi
fi

export CYCLAW_HOME="$HOME_DIR"
export CYCLAW_REPO="$REPO_DIR"
export CYCLAW_HARNESS_PORT="$PORT"
# CYCLAW_API_KEY is passed through from the caller's environment, never
# generated or defaulted here, and deliberately NOT written into the installed
# shim (that would put the secret in a profile file on disk). Unset means the
# console's state-changing routes fail closed with 401 -- paste the key into
# the console's key field, or set the variable before launching.

echo "[cyclaw] repo    : $REPO_DIR"
echo "[cyclaw] home    : $HOME_DIR"
echo "[cyclaw] console : http://127.0.0.1:$PORT  (Ctrl+C to stop)"
if [ -z "${CYCLAW_API_KEY:-}" ]; then
  echo "[cyclaw] warn    : CYCLAW_API_KEY not set - state-changing console routes will return 401" >&2
fi

if [ "$NO_BROWSER" -eq 0 ]; then
  # Open the browser slightly after the server starts; the page retries until
  # the API answers, so a race here is harmless. macOS ships `open`; Linux
  # desktops ship `xdg-open` -- best-effort, never fatal if neither exists
  # (a headless box, or a machine with no browser installed).
  (
    sleep 2
    if command -v open >/dev/null 2>&1; then
      open "http://127.0.0.1:$PORT"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1
    fi
  ) &
  disown 2>/dev/null || true
fi

cd "$REPO_DIR"
exec "$VENV_PY" -m harness.server
