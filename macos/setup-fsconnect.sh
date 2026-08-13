#!/usr/bin/env bash
# Prepare ~/CyClaw-FS and enable the confined macOS list/stat/read profile.
# Compatible with macOS's stock Bash 3.2 and BSD userland.

set -euo pipefail

PREPARE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prepare-only) PREPARE_ONLY=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

step() { printf '[cyclaw] %s\n' "$1"; }
warn() { printf '[cyclaw] WARNING: %s\n' "$1" >&2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_PATH="${CYCLAW_FSCONNECT_CONFIG:-$SCRIPT_DIR/../config.yaml}"
FS_ROOT="$HOME/CyClaw-FS"
README_PATH="$FS_ROOT/README.txt"

if [ -L "$FS_ROOT" ] || { [ -e "$FS_ROOT" ] && [ ! -d "$FS_ROOT" ]; }; then
  echo "cyclaw: refusing non-directory or symlink fsconnect jail: $FS_ROOT" >&2
  exit 1
fi
mkdir -p "$FS_ROOT"
chmod 700 "$FS_ROOT"

if [ -L "$README_PATH" ] || { [ -e "$README_PATH" ] && [ ! -f "$README_PATH" ]; }; then
  echo "cyclaw: refusing non-file or symlink jail README: $README_PATH" >&2
  exit 1
fi
if [ ! -e "$README_PATH" ]; then
  cat > "$README_PATH" <<'README'
CyClaw read/list jail

CyClaw is configured to list, stat, and read files only inside this folder.
Writes and indexing are off. Do not store secrets here if you later enable indexing.
README
  chmod 600 "$README_PATH"
fi
step "fsconnect jail ready at $FS_ROOT"

if command -v tmutil >/dev/null 2>&1; then
  if tmutil addexclusion "$FS_ROOT" >/dev/null 2>&1; then
    step "requested a Time Machine exclusion for $FS_ROOT"
  else
    warn "tmutil could not exclude $FS_ROOT; continuing"
  fi
fi

if [ "$PREPARE_ONLY" -eq 1 ]; then
  step "prepare-only complete; config.yaml was not changed"
  exit 0
fi

find_config_python() {
  local candidate
  if [ -n "${CYCLAW_FSCONNECT_PYTHON:-}" ]; then
    if "$CYCLAW_FSCONNECT_PYTHON" -c 'import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$CYCLAW_FSCONNECT_PYTHON"
      return 0
    fi
    return 1
  fi
  for candidate in "$HOME/.CyClaw/venv/bin/python" python3.12 python3 python; do
    if { [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; } && \
       "$candidate" -c 'import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_CMD="$(find_config_python)"; then
  echo "cyclaw: Python 3.12+ with PyYAML is required to enable fsconnect safely." >&2
  echo "        Re-run the installer without --skip-python-deps, or use --no-fsconnect." >&2
  exit 1
fi

"$PYTHON_CMD" "$SCRIPT_DIR/_enable_fsconnect_readlist.py" \
  --config "$CONFIG_PATH" \
  --root "$FS_ROOT"

step "list/stat/read enabled; writes and indexing remain off"
printf '%s\n' \
  "Next steps (run from the CyClaw repo):" \
  "  python -m agentic.fsconnect.cli status" \
  "  python -m agentic.fsconnect.cli list --root \"$FS_ROOT\""
