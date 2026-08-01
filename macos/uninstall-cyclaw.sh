#!/usr/bin/env bash
# Removes the CyClaw harness integration from the current user's environment
# (macOS/Linux). Removes the cyclaw() shell function and the ~/.CyClaw/bin
# PATH entry -- install-cyclaw.sh's two independent marker blocks in the shell
# rc file. The home directory (sessions, venv, repo clone) is KEPT by default
# so no data is lost; pass --remove-home to delete it (prompts first).
#
# Usage:
#   bash macos/uninstall-cyclaw.sh                # keep ~/.CyClaw data
#   bash macos/uninstall-cyclaw.sh --remove-home  # also delete ~/.CyClaw

set -euo pipefail

REMOVE_HOME=0
while [ $# -gt 0 ]; do
  case "$1" in
    --remove-home) REMOVE_HOME=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

HOME_DIR="$HOME/.CyClaw"

detect_rc_file() {
  case "${SHELL:-}" in
    */zsh) echo "$HOME/.zshrc" ;;
    *)
      if [ -f "$HOME/.bash_profile" ]; then echo "$HOME/.bash_profile"
      else echo "$HOME/.bashrc"
      fi
      ;;
  esac
}
RC_FILE="$(detect_rc_file)"

# awk's -v is POSIX and behaves identically under macOS's stock "one true awk"
# and GNU awk, unlike sed's -i (BSD and GNU sed take incompatible -i syntax) --
# this is why awk was chosen here over a sed in-place edit.
remove_block() {
  local marker_start="$1" marker_end="$2"
  if [ -f "$RC_FILE" ] && grep -qF "$marker_start" "$RC_FILE" 2>/dev/null; then
    awk -v s="$marker_start" -v e="$marker_end" '
      $0 == s { skip = 1; next }
      $0 == e { skip = 0; next }
      !skip { print }
    ' "$RC_FILE" > "$RC_FILE.cyclaw-tmp" && mv "$RC_FILE.cyclaw-tmp" "$RC_FILE"
    echo "[cyclaw] removed block ($marker_start) from $RC_FILE"
  fi
}

remove_block "# >>> cyclaw harness path >>>" "# <<< cyclaw harness path <<<"
remove_block "# >>> cyclaw harness >>>" "# <<< cyclaw harness <<<"

if [ "$REMOVE_HOME" -eq 1 ] && [ -d "$HOME_DIR" ]; then
  printf 'Delete %s including all sessions and the venv? (y/N) ' "$HOME_DIR"
  read -r answer
  case "$answer" in
    y|Y)
      rm -rf "$HOME_DIR"
      echo "[cyclaw] removed $HOME_DIR"
      ;;
    *)
      echo "[cyclaw] kept $HOME_DIR"
      ;;
  esac
fi

echo "[cyclaw] uninstall complete."
