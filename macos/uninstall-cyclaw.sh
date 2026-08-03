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

PATH_START="# >>> cyclaw harness path >>>"
PATH_END="# <<< cyclaw harness path <<<"
FUNC_START="# >>> cyclaw harness >>>"
FUNC_END="# <<< cyclaw harness <<<"

resolve_edit_path() {
  local path="$1" target dir hops=0
  while [ -L "$path" ]; do
    hops=$((hops + 1))
    [ "$hops" -le 40 ] || return 1
    target="$(readlink "$path")" || return 1
    case "$target" in
      /*) path="$target" ;;
      *)
        dir="${path%/*}"
        [ -n "$dir" ] || dir="/"
        path="$dir/$target"
        ;;
    esac
  done
  [ -f "$path" ] || return 1
  printf '%s\n' "$path"
}

# awk's -v is POSIX and behaves identically under macOS's stock "one true awk"
# and GNU awk, unlike sed's -i (BSD and GNU sed take incompatible -i syntax).
# Both blocks are validated before one atomic replacement so malformed markers
# can never cause a partial edit of a user-owned startup file.
remove_managed_blocks() {
  local edit_path="$1" display_path="$2" tmp_dir tmp
  if ! grep -qxF "$PATH_START" "$edit_path" 2>/dev/null && \
     ! grep -qxF "$FUNC_START" "$edit_path" 2>/dev/null; then
    return 0
  fi

  tmp_dir="$(mktemp -d "$edit_path.cyclaw-tmp.XXXXXX")"
  tmp="$tmp_dir/rc"
  if ! cp -p "$edit_path" "$tmp"; then
    rmdir "$tmp_dir"
    return 1
  fi

  if awk -v ps="$PATH_START" -v pe="$PATH_END" \
         -v fs="$FUNC_START" -v fe="$FUNC_END" '
    $0 == ps {
      if (block != "") bad = 1
      block = "path"
      next
    }
    $0 == fs {
      if (block != "") bad = 1
      block = "func"
      next
    }
    $0 == pe {
      if (block != "path") bad = 1
      else block = ""
      next
    }
    $0 == fe {
      if (block != "func") bad = 1
      else block = ""
      next
    }
    block == "" { print }
    END {
      if (block != "") bad = 1
      if (bad) exit 2
    }
  ' "$edit_path" > "$tmp"; then
    mv "$tmp" "$edit_path"
    rmdir "$tmp_dir"
    echo "[cyclaw] removed managed blocks from $display_path"
  else
    rm -f "$tmp"
    rmdir "$tmp_dir"
    echo "[cyclaw] WARNING: malformed managed block in $display_path; left unchanged" >&2
  fi
}

# Clean every supported startup file. This also removes legacy macOS bash
# blocks that pre-fix installers wrote to ~/.bashrc.
for RC_FILE in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile" "$HOME/.bashrc"; do
  if [ ! -e "$RC_FILE" ] && [ ! -L "$RC_FILE" ]; then
    continue
  fi
  if ! EDIT_PATH="$(resolve_edit_path "$RC_FILE")"; then
    echo "[cyclaw] WARNING: $RC_FILE does not resolve to a regular file; left unchanged" >&2
    continue
  fi
  remove_managed_blocks "$EDIT_PATH" "$RC_FILE"
done

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
