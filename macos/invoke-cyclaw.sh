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

if [ -z "${CYCLAW_API_KEY:-}" ]; then
  echo "[cyclaw] warn : CYCLAW_API_KEY not set — state-changing console routes will return 401" >&2
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
  for i in 1 2 3 4 5; do
    if curl -sf "http://127.0.0.1:$GATE_PORT/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.4
  done
fi

# --- start coding harness ---
if [ "$NO_HARNESS" -eq 0 ]; then
  if [ ! -f "$REPO_DIR/harness/server.py" ]; then
    echo "[cyclaw] error: harness/server.py not found — cannot start harness" >&2
    exit 1
  fi
  "$VENV_PY" -m harness.server &
  HARNESS_PID=$!
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

# Wait on whichever process(es) we started.
if [ -n "$HARNESS_PID" ]; then
  wait "$HARNESS_PID"
elif [ -n "$GATE_PID" ]; then
  wait "$GATE_PID"
else
  echo "[cyclaw] nothing started (--no-gate and --no-harness both set)" >&2
  exit 1
fi
