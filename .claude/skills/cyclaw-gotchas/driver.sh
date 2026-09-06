#!/usr/bin/env bash
# cyclaw-gotchas driver -- the Claude Code sandbox path that actually worked.
# Every subcommand below was run end-to-end in a fresh cloud sandbox on
# 2026-09-06 (Ubuntu 24.04 image, egress proxy denying download.pytorch.org
# and huggingface.co). Run from the repo root:
#   bash .claude/skills/cyclaw-gotchas/driver.sh <cmd> [args]
#
#   inventory   branch vs origin/main, interpreters, tools, proxy-denied hosts,
#               venv/index state. Stdlib + git + curl only; never fails.
#   venv        build the Python 3.12 venv OUTSIDE the repo tree. Tries the
#               torch CPU index first; when the proxy denies it, falls back to
#               plain torch from PyPI (pulls the CUDA deps, ~2 GB, ~7 min).
#   serve       launch gate.py in the background with a pidfile; wait for /health.
#   probe       curl /health, POST /query, GET /soul without and with the key.
#   stop        kill the server `serve` started (pidfile, not pkill -f).
#   test [...]  pytest through the venv with pyproject's addopts cleared so the
#               "N passed" summary prints; exit code propagates. Default: tests/.
#   checks      the stdlib guards: invariant-guard, doc-sync, config-guard,
#               dep-guard, verify-deps env drift. Exit 1 if any fails.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT" || exit 3
VENV="${CYCLAW_VENV:-/root/.venv-cyclaw-312}"
PY="$VENV/bin/python"
SCRATCH="${CYCLAW_SCRATCH:-/tmp/cyclaw-gotchas}"
PORT="${PORT:-8787}"
BASE="http://127.0.0.1:$PORT"  # DevSkim: ignore DS162092 -- loopback by design
PIDFILE="$SCRATCH/gate.pid"
LOG="$SCRATCH/gate.log"
mkdir -p "$SCRATCH"

need_venv() {
  if [ ! -x "$PY" ]; then
    echo "no venv at $VENV -- run: bash .claude/skills/cyclaw-gotchas/driver.sh venv" >&2
    exit 3
  fi
}

cmd_inventory() {
  echo "== cyclaw-gotchas inventory =="
  echo "repo:      $REPO_ROOT"
  echo "branch:    $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git rev-parse --short HEAD 2>/dev/null)"
  if git rev-parse --verify -q origin/main >/dev/null; then
    read -r behind ahead < <(git rev-list --left-right --count origin/main...HEAD)
    echo "vs main:   ahead $ahead, behind $behind"
  fi
  echo "dirty:     $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') path(s)"
  echo "python3:   $(python3 --version 2>&1)  ($(command -v python3))"
  echo "3.12:      $(command -v python3.12 || echo MISSING)"
  if [ -x "$PY" ] && "$PY" -c "import torch, chromadb, langgraph, pytest" 2>/dev/null; then
    echo "venv:      $VENV ready (torch/chromadb/langgraph/pytest import)"
  elif [ -x "$PY" ]; then
    echo "venv:      $VENV present but deps incomplete -- run: driver.sh venv"
  else
    echo "venv:      absent -- run: driver.sh venv"
  fi
  for t in ruff uv docker; do printf "%-10s %s\n" "$t:" "$(command -v "$t" || echo absent)"; done
  if [ -f index/bm25.json ]; then echo "index:     built"; else echo "index:     absent (/query answers 503 INDEX_NOT_FOUND)"; fi
  if [ -n "${HTTPS_PROXY:-}" ]; then
    denied=$(curl -sS --max-time 5 "$HTTPS_PROXY/__agentproxy/status" 2>/dev/null \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(' '.join(sorted({f['host'] for f in d.get('recentRelayFailures',[])})) or 'none seen yet')" 2>/dev/null)
    echo "proxy:     denied so far: ${denied:-unreadable}"
  else
    echo "proxy:     HTTPS_PROXY unset (not the cloud sandbox)"
  fi
  echo "disk free: $(df -h "$REPO_ROOT" 2>/dev/null | awk 'NR==2{print $4}')"
  return 0
}

cmd_venv() {
  if [ -x "$PY" ] && "$PY" -c "import torch, chromadb, langgraph, pytest" 2>/dev/null; then
    echo "venv ready at $VENV"; return 0
  fi
  command -v python3.12 >/dev/null || { echo "python3.12 not on PATH" >&2; return 3; }
  [ -x "$PY" ] || python3.12 -m venv "$VENV" || return 3
  local pip="$VENV/bin/pip"
  if "$PY" -c "import torch" 2>/dev/null; then
    echo "torch already importable"
  elif "$pip" install -q --retries 1 --timeout 15 "torch==2.13.0+cpu" --index-url https://download.pytorch.org/whl/cpu; then
    echo "torch: installed from the CPU index"
  else
    echo "torch: CPU index unreachable (proxy policy denies download.pytorch.org)."
    echo "       Falling back to plain torch==2.13.0 from PyPI. --no-deps does NOT work"
    echo "       (import fails on libcudart.so.13); the CUDA deps come along, ~2 GB."
    "$pip" install -q "torch==2.13.0" || return 2
  fi
  grep -v -e '^torch==' -e '^--extra-index-url https://download.pytorch.org' requirements.txt > "$SCRATCH/requirements-notorch.txt"
  grep -v '^torch==' constraints.txt > "$SCRATCH/constraints-notorch.txt"
  "$pip" install -q -r "$SCRATCH/requirements-notorch.txt" -r requirements-test.txt \
      -c "$SCRATCH/constraints-notorch.txt" --ignore-installed PyYAML || return 2
  "$PY" -c "import torch, chromadb, langgraph, pytest; print('venv ready:', torch.__version__)"
}

cmd_serve() {
  need_venv
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "already running (pid $(cat "$PIDFILE"))"; return 0
  fi
  mkdir -p logs
  GROK_API_KEY="${GROK_API_KEY:-dummy}" CYCLAW_API_KEY="${CYCLAW_API_KEY:-smoke-test-key}" \
    nohup "$PY" gate.py > "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  local i
  for i in $(seq 1 90); do
    curl -s --max-time 2 "$BASE/health" >/dev/null 2>&1 && { echo "gate up after ${i}s (pid $(cat "$PIDFILE"), log $LOG)"; return 0; }
    kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { echo "gate exited early; tail of $LOG:"; tail -20 "$LOG"; rm -f "$PIDFILE"; return 2; }
    sleep 1
  done
  echo "gate not healthy after 90s; tail of $LOG:"; tail -20 "$LOG"; return 2
}

cmd_probe() {
  local key="${CYCLAW_API_KEY:-smoke-test-key}"
  echo "--- GET /health (degraded without Ollama is NORMAL)"
  curl -s --max-time 5 "$BASE/health" | python3 -c "import json,sys; d=json.load(sys.stdin); print({k: d.get(k) for k in ('status','index_ready','graph_ready','mode')})"
  echo "--- POST /query (503 INDEX_NOT_FOUND until the index is built)"
  curl -s --max-time 30 -X POST "$BASE/query" -H 'Content-Type: application/json' \
    -d '{"query":"what is the triple gate"}' -w " [%{http_code}]"; echo
  echo "--- GET /soul without key (expect 401 -- unset/wrong key fails CLOSED)"
  curl -s -o /dev/null -w "%{http_code}\n" "$BASE/soul"
  echo "--- GET /soul with key (expect 200)"
  curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $key" "$BASE/soul"
}

cmd_stop() {
  if [ -f "$PIDFILE" ]; then
    local pid; pid="$(cat "$PIDFILE")"
    kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null; rm -f "$PIDFILE"
    echo "stopped pid $pid"
  else
    echo "no pidfile at $PIDFILE"
  fi
}

cmd_test() {
  need_venv
  # pyproject's addopts already carries -q; passing -q again makes pytest -qq,
  # which drops the final "N passed" line. Clear addopts and set them once here.
  GROK_API_KEY="${GROK_API_KEY:-dummy}" "$PY" -m pytest "${@:-tests/}" -o addopts="" -q --tb=short \
    -p no:cacheprovider -W ignore::DeprecationWarning
  local rc=$?
  echo "pytest exit=$rc"
  return $rc
}

cmd_checks() {
  local failed=0 rc
  run_check() {
    local label="$1"; shift
    "$@" > "$SCRATCH/check-$label.log" 2>&1; rc=$?
    printf "%-16s exit=%s  %s\n" "$label" "$rc" "$(tail -1 "$SCRATCH/check-$label.log")"
    [ "$rc" -eq 0 ] || failed=1
  }
  run_check invariant-guard python3 .claude/skills/invariant-guard/check_invariants.py
  run_check doc-sync        python3 .claude/skills/doc-sync/doc_sync.py
  run_check config-guard    python3 .claude/skills/config-guard/check_config.py
  run_check dep-guard       python3 .claude/skills/dep-guard/check_deps.py
  run_check env-drift       python3 .claude/skills/verify-deps/check_env_drift.py --strict
  echo "logs: $SCRATCH/check-*.log"
  return $failed
}

case "${1:-}" in
  inventory) cmd_inventory ;;
  venv)      cmd_venv ;;
  serve)     cmd_serve ;;
  probe)     cmd_probe ;;
  stop)      cmd_stop ;;
  test)      shift; cmd_test "$@" ;;
  checks)    cmd_checks ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
