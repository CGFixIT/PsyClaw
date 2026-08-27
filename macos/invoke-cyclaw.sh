#!/usr/bin/env bash
# Launches CyClaw RAG gateway (terminal.html) + coding harness.
#
# macOS (Apple Silicon arm64) and Linux, bash or zsh.
# - RAG gateway (gate.py / static/terminal.html): 127.0.0.1:8787
# - Coding harness control plane:               127.0.0.1:8790
# Uses the per-user venv under ~/.CyClaw/venv and the repo at $CYCLAW_REPO
# (or ~/.CyClaw/repo). Ctrl+C stops both servers.
#
# Usage:
#   cyclaw
#   bash macos/invoke-cyclaw.sh --no-browser --port 8800
#   bash macos/invoke-cyclaw.sh --gate-port 8788 --port 8791
#
# Options:
#   --port PORT       harness console port (default 8790)
#   --gate-port PORT  RAG gateway / terminal.html port (default 8787)
#   --no-browser      do not open browsers; just serve
#   --repo PATH       explicit path to the CyClaw checkout (overrides $CYCLAW_REPO)
#   --no-gate         skip starting the RAG gateway (harness only)
#   --no-harness      skip starting the harness (gateway only)

set -euo pipefail

PORT="${CYCLAW_HARNESS_PORT:-8790}"
GATE_PORT="${CYCLAW_GATE_PORT:-8787}"
NO_BROWSER=0
NO_GATE=0
NO_HARNESS=0
REPO_OVERRIDE=""

# Validate a port before it reaches `uvicorn --port` and the printed URLs.
# Without this a typo ("--port 87go") is echoed as a working-looking URL and
# then fails deep inside uvicorn's own argument parsing, or -- worse for the
# harness, which reads CYCLAW_HARNESS_PORT from the environment -- surfaces as
# a confusing config error far from the actual mistake.
require_port() {
  case "$2" in
    ''|*[!0-9]*)
      echo "$1 requires a numeric port (got '$2')" >&2
      exit 1
      ;;
  esac
  if [ "$2" -lt 1 ] || [ "$2" -gt 65535 ]; then
    echo "$1 must be between 1 and 65535 (got '$2')" >&2
    exit 1
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --port)
      PORT="${2:?--port requires a value}"
      shift 2
      ;;
    --gate-port)
      GATE_PORT="${2:?--gate-port requires a value}"
      shift 2
      ;;
    --no-browser)
      NO_BROWSER=1
      shift
      ;;
    --no-gate)
      NO_GATE=1
      shift
      ;;
    --no-harness)
      NO_HARNESS=1
      shift
      ;;
    --repo)
      REPO_OVERRIDE="${2:?--repo requires a value}"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Validated after the parse loop so the env-var defaults above (CYCLAW_*_PORT)
# get the same check as the flags -- an operator who exports a bad value in
# their rc file would otherwise skip validation entirely.
require_port "harness port (--port / CYCLAW_HARNESS_PORT)" "$PORT"
require_port "gate port (--gate-port / CYCLAW_GATE_PORT)" "$GATE_PORT"

HOME_DIR="${CYCLAW_HOME:-$HOME/.CyClaw}"
if [ -n "$REPO_OVERRIDE" ]; then
  REPO_DIR="$REPO_OVERRIDE"
else
  REPO_DIR="${CYCLAW_REPO:-$HOME_DIR/repo}"
fi

VENV_PY="$HOME_DIR/venv/bin/python"
if [ ! -f "$REPO_DIR/harness/server.py" ] && [ ! -f "$REPO_DIR/gate.py" ]; then
  echo "CyClaw repo not found at '$REPO_DIR' (missing harness/server.py or gate.py). Run install-cyclaw.sh first (or pass --repo)." >&2
  exit 1
fi
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="$(command -v python3 || true)"
  if [ -z "$VENV_PY" ]; then
    echo "No venv at $HOME_DIR/venv and no python3 on PATH. Re-run install-cyclaw.sh." >&2
    exit 1
  fi
fi

export CYCLAW_HOME="$HOME_DIR"
export CYCLAW_REPO="$REPO_DIR"
export CYCLAW_HARNESS_PORT="$PORT"
export CYCLAW_GATE_PORT="$GATE_PORT"

echo "[cyclaw] repo     : $REPO_DIR"
echo "[cyclaw] home     : $HOME_DIR"
if [ "$NO_GATE" -eq 0 ]; then
  echo "[cyclaw] terminal : http://127.0.0.1:$GATE_PORT  (RAG gateway / static/terminal.html)"
fi
if [ "$NO_HARNESS" -eq 0 ]; then
  echo "[cyclaw] harness  : http://127.0.0.1:$PORT"
fi
echo "[cyclaw] Ctrl+C stops all started servers"

# Load persisted keys into THIS process so gate/harness inherit them.
# ~/.CyClaw/.env is chmod 600 and gitignored. Never print its contents.
# xtrace would dump every assignment — refuse rather than leak.
# ponytail: one copy here (shim + cyclaw() + direct script all exec this).
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

if [ -z "${CYCLAW_API_KEY:-}" ]; then
  case "$-" in
    *x*) echo "[cyclaw] error: refusing to source .env with xtrace on (would print secrets). Re-run without bash -x." >&2; exit 1 ;;
  esac
  # Chained on the result, not `-f`: a refused HOME file must not shadow the repo copy.
  _source_dotenv "$HOME_DIR/.env" || _source_dotenv "$REPO_DIR/.env" || true
fi

if [ -z "${CYCLAW_API_KEY:-}" ]; then
  echo "[cyclaw] warn : CYCLAW_API_KEY not set — Soul / ops / harness state-changing routes will 401. Typing the key in the browser cannot configure the server; source ~/.CyClaw/.env or set the env var, then restart." >&2
fi

# --- cleanup on exit / signals ---
GATE_PID=""
HARNESS_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [ -n "$GATE_PID" ] && kill -0 "$GATE_PID" 2>/dev/null; then
    echo "[cyclaw] stopping RAG gateway (pid $GATE_PID)..."
    kill "$GATE_PID" 2>/dev/null || true
    wait "$GATE_PID" 2>/dev/null || true
  fi
  if [ -n "$HARNESS_PID" ] && kill -0 "$HARNESS_PID" 2>/dev/null; then
    echo "[cyclaw] stopping harness (pid $HARNESS_PID)..."
    kill "$HARNESS_PID" 2>/dev/null || true
    wait "$HARNESS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$REPO_DIR"

# --- start RAG gateway (serves terminal.html) ---
if [ "$NO_GATE" -eq 0 ]; then
  if [ ! -f "$REPO_DIR/gate.py" ]; then
    echo "[cyclaw] error: gate.py not found at $REPO_DIR — cannot start terminal.html server" >&2
    exit 1
  fi
  if "$VENV_PY" -c "import uvicorn" 2>/dev/null; then
    "$VENV_PY" -m uvicorn gate:app --host 127.0.0.1 --port "$GATE_PORT" --log-level warning &
  else
    echo "[cyclaw] error: uvicorn not available in $VENV_PY. Install deps first." >&2
    exit 1
  fi
  GATE_PID=$!
  # Poll /health, but check the process is still alive on each pass. gate.py
  # exits fast on a missing retrieval index, an already-bound port, or an
  # invalid config -- and the old loop only ever polled the socket, so a gate
  # that died on startup fell through silently: the harness still started, a
  # browser still opened on a dead port, and `wait "$HARNESS_PID"` below then
  # blocked forever with no diagnostic. Surface the real cause instead.
  GATE_READY=0
  for i in 1 2 3 4 5; do
    if ! kill -0 "$GATE_PID" 2>/dev/null; then
      wait "$GATE_PID" 2>/dev/null || true
      # Cleared first so the EXIT trap's cleanup() does not try to kill a pid
      # that has already been reaped.
      GATE_PID=""
      echo "[cyclaw] error: RAG gateway exited during startup (port $GATE_PORT)." >&2
      echo "[cyclaw]        Common causes: the retrieval index is not built" >&2
      echo "[cyclaw]        (run '\"$VENV_PY\" -m retrieval.indexer'), port $GATE_PORT is" >&2
      echo "[cyclaw]        already in use, or config.yaml is invalid." >&2
      exit 1
    fi
    if curl -sf "http://127.0.0.1:$GATE_PORT/health" >/dev/null 2>&1; then
      GATE_READY=1
      break
    fi
    sleep 0.4
  done
  # Still alive but not answering yet is normal on a cold start (the embedding
  # model and index load lazily), so warn rather than abort.
  if [ "$GATE_READY" -eq 0 ]; then
    echo "[cyclaw] warn : RAG gateway not answering /health yet; still starting (pid $GATE_PID)" >&2
  fi
fi

# --- start coding harness ---
if [ "$NO_HARNESS" -eq 0 ]; then
  if [ ! -f "$REPO_DIR/harness/server.py" ]; then
    echo "[cyclaw] error: harness/server.py not found — cannot start harness" >&2
    exit 1
  fi
  "$VENV_PY" -m harness.server &
  HARNESS_PID=$!
  # Same startup-death race as the gateway above: the harness can fail fast on
  # a missing dependency or an already-bound port. Probe /api/status while also
  # checking the process is still alive, so a startup failure surfaces before
  # we open a browser on a dead port and block forever in wait.
  HARNESS_READY=0
  for i in 1 2 3 4 5; do
    if ! kill -0 "$HARNESS_PID" 2>/dev/null; then
      wait "$HARNESS_PID" 2>/dev/null || true
      HARNESS_PID=""
      echo "[cyclaw] error: coding harness exited during startup (port $PORT)." >&2
      echo "[cyclaw]        Common causes: port $PORT is already in use or a dependency is missing." >&2
      exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
      HARNESS_READY=1
      break
    fi
    sleep 0.4
  done
  if [ "$HARNESS_READY" -eq 0 ]; then
    echo "[cyclaw] warn : coding harness not answering /api/status yet; still starting (pid $HARNESS_PID)" >&2
  fi
fi

# --- open browsers (best-effort) ---
if [ "$NO_BROWSER" -eq 0 ]; then
  (
    sleep 1.5
    if command -v open >/dev/null 2>&1; then
      [ "$NO_GATE" -eq 0 ] && open "http://127.0.0.1:$GATE_PORT"
      [ "$NO_HARNESS" -eq 0 ] && open "http://127.0.0.1:$PORT"
    elif command -v xdg-open >/dev/null 2>&1; then
      [ "$NO_GATE" -eq 0 ] && xdg-open "http://127.0.0.1:$GATE_PORT" >/dev/null 2>&1
      [ "$NO_HARNESS" -eq 0 ] && xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1
    fi
  ) &
  disown 2>/dev/null || true
fi

# Wait on whichever process(es) we started. A single `wait "$HARNESS_PID"`
# blocked forever if the harness died first and the gateway was still running
# (or vice versa), because wait only returns when its target exits. Poll both
# pids so any death triggers cleanup and exits the script.
if [ -z "$HARNESS_PID" ] && [ -z "$GATE_PID" ]; then
  echo "[cyclaw] nothing started (--no-gate and --no-harness both set)" >&2
  exit 1
fi
CHILD_EXIT_STATUS=0
while true; do
  if [ -n "$HARNESS_PID" ] && ! kill -0 "$HARNESS_PID" 2>/dev/null; then
    echo "[cyclaw] harness process (pid $HARNESS_PID) exited" >&2
    if wait "$HARNESS_PID" 2>/dev/null; then
      CHILD_EXIT_STATUS=0
    else
      CHILD_EXIT_STATUS=$?
    fi
    HARNESS_PID=""
    break
  fi
  if [ -n "$GATE_PID" ] && ! kill -0 "$GATE_PID" 2>/dev/null; then
    echo "[cyclaw] RAG gateway process (pid $GATE_PID) exited" >&2
    if wait "$GATE_PID" 2>/dev/null; then
      CHILD_EXIT_STATUS=0
    else
      CHILD_EXIT_STATUS=$?
    fi
    GATE_PID=""
    break
  fi
  # Wait a bit; any signal still fires the cleanup trap.
  # Do not use bash-4.3 wait-any here: macOS /bin/bash is 3.2.
  # Reap the specific dead pid above instead.
  sleep 1
done
exit "$CHILD_EXIT_STATUS"
