#!/usr/bin/env bash
# Removes the CyClaw harness integration from the current user's environment
# (macOS/Linux). Removes the cyclaw() shell function, the ~/.CyClaw/bin
# PATH entry, and the `# >>> cyclaw keys >>>` source block -- install-cyclaw.sh
# and setup-cyclaw-keys.sh's independent marker blocks in the shell rc file.
# The home directory (sessions, venv, repo clone, .env) is KEPT by default
# so no data is lost; pass --remove-home to delete it (prompts first).
#
# Usage:
#   bash macos/uninstall-cyclaw.sh                # keep ~/.CyClaw data
#   bash macos/uninstall-cyclaw.sh --remove-home  # also delete ~/.CyClaw
#   bash macos/uninstall-cyclaw.sh --remove-fsconnect  # prompt before deleting ~/CyClaw-FS

set -euo pipefail

REMOVE_HOME=0
REMOVE_FSCONNECT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --remove-home) REMOVE_HOME=1; shift ;;
    --remove-fsconnect) REMOVE_FSCONNECT=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

HOME_DIR="$HOME/.CyClaw"
FSCONNECT_DIR="$HOME/CyClaw-FS"

# -- Sync scheduler cleanup ---------------------------------------------------
# The Dropbox sync job (docs/SYNC_README.md) is tagged system-wide -- one
# crontab comment / one launchd Label per operator account, regardless of
# which CyClaw checkout registered it -- so at most one is ever active. Clean
# it up FIRST, before any --remove-home prompt below could delete the repo/venv
# this needs to invoke, so a background job never outlives `cyclaw` itself.
# Best-effort only: a missing/invalid config.yaml, an unconfigured sync: block,
# or no registered schedule are all normal, non-fatal outcomes here -- this
# must never abort the rest of uninstall (rc-block cleanup, --remove-home,
# --remove-fsconnect) over an unrelated sync problem.
unschedule_sync_job() {
  local py=""
  if [ -x "$HOME_DIR/venv/bin/python" ]; then
    py="$HOME_DIR/venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    py="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    py="$(command -v python)"
  fi
  if [ -z "$py" ] || [ ! -f "$HOME_DIR/repo/config.yaml" ]; then
    return 0
  fi
  echo "[cyclaw] checking for a registered sync schedule..."
  # --config is passed explicitly and absolute: sync.cli's own default
  # ("config.yaml") resolves relative to wherever the imported sync/utils
  # package physically lives on disk (utils.logger.resolve_config_path is
  # repo-root-anchored via __file__, not cwd) -- normally that IS
  # $HOME_DIR/repo since it's the only copy on PYTHONPATH, but relying on
  # that implicitly is fragile. Being explicit here removes the ambiguity.
  if ! (cd "$HOME_DIR/repo" && "$py" -m sync.cli --config "$HOME_DIR/repo/config.yaml" unschedule); then
    echo "[cyclaw] WARNING: could not clean up the sync schedule (see above); remove it manually with 'python -m sync.cli unschedule' if needed" >&2
  fi
}
unschedule_sync_job

# -- Landed LaunchAgent cleanup -----------------------------------------------
# #910/#911 added generators for telegram-poll (KeepAlive), telegram-health,
# and fsconnect-trash. #912's generate_service_plist.py uses the gate/harness
# labels. None of these jobs go through sync.cli, so the step above never
# sees them -- a loaded telegram-poll KeepAlive or crash-restart gate agent
# would keep running after `cyclaw` is gone. Best-effort: bootout the
# launchd label even if the plist file is already gone (a KeepAlive job
# can stay loaded after a hand-deleted plist). Then delete the file if
# it is still present. Failures print a WARNING and uninstall continues.
# Booting out a label that was never generated is a silent no-op.
unschedule_landed_launchagents() {
  local uid dest label
  uid="$(id -u 2>/dev/null || echo 0)"
  for label in \
    com.cgfixit.cyclaw.telegram-poll \
    com.cgfixit.cyclaw.telegram-health \
    com.cgfixit.cyclaw.fsconnect-trash \
    com.cgfixit.cyclaw.gate \
    com.cgfixit.cyclaw.harness \
    com.cgfixit.cyclaw.keys-rotate
  do
    dest="$HOME/Library/LaunchAgents/${label}.plist"
    if [ "$(uname -s)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
      launchctl bootout "gui/${uid}/${label}" 2>/dev/null || true
    fi
    if [ ! -f "$dest" ]; then
      continue
    fi
    echo "[cyclaw] removing LaunchAgent $label..."
    if ! rm -f "$dest"; then
      echo "[cyclaw] WARNING: could not remove $dest" >&2
    fi
  done
}
unschedule_landed_launchagents

PATH_START="# >>> cyclaw harness path >>>"
PATH_END="# <<< cyclaw harness path <<<"
FUNC_START="# >>> cyclaw harness >>>"
FUNC_END="# <<< cyclaw harness <<<"
KEYS_START="# >>> cyclaw keys >>>"
KEYS_END="# <<< cyclaw keys <<<"

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
     ! grep -qxF "$FUNC_START" "$edit_path" 2>/dev/null && \
     ! grep -qxF "$KEYS_START" "$edit_path" 2>/dev/null; then
    return 0
  fi

  tmp_dir="$(mktemp -d "$edit_path.cyclaw-tmp.XXXXXX")"
  tmp="$tmp_dir/rc"
  if ! cp -p "$edit_path" "$tmp"; then
    rmdir "$tmp_dir"
    return 1
  fi

  if awk -v ps="$PATH_START" -v pe="$PATH_END" \
         -v fs="$FUNC_START" -v fe="$FUNC_END" \
         -v ks="$KEYS_START" -v ke="$KEYS_END" '
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
    $0 == ks {
      if (block != "") bad = 1
      block = "keys"
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
    $0 == ke {
      if (block != "keys") bad = 1
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
  answer=""
  read -r answer || true
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

if [ "$REMOVE_FSCONNECT" -eq 1 ] && { [ -e "$FSCONNECT_DIR" ] || [ -L "$FSCONNECT_DIR" ]; }; then
  if [ -L "$FSCONNECT_DIR" ] || [ "$FSCONNECT_DIR" != "$HOME/CyClaw-FS" ]; then
    echo "[cyclaw] WARNING: refusing unexpected fsconnect target: $FSCONNECT_DIR" >&2
    exit 1
  fi
  printf 'Delete %s and every file in the fsconnect jail? (y/N) ' "$FSCONNECT_DIR"
  answer=""
  read -r answer || true
  case "$answer" in
    y|Y)
      rm -rf "$FSCONNECT_DIR"
      echo "[cyclaw] removed $FSCONNECT_DIR (config remains fail-closed until setup is rerun)"
      ;;
    *)
      echo "[cyclaw] kept $FSCONNECT_DIR"
      ;;
  esac
elif [ "$REMOVE_FSCONNECT" -eq 0 ] && [ -d "$FSCONNECT_DIR" ]; then
  echo "[cyclaw] kept $FSCONNECT_DIR (pass --remove-fsconnect to remove it)"
fi

echo "[cyclaw] uninstall complete."
