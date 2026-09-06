#!/usr/bin/env bash
# Removes the CyClaw harness integration from the current user's environment
# (macOS/Linux). Removes the cyclaw() shell function, the ~/.CyClaw/bin
# PATH entry, and the `# >>> cyclaw keys >>>` source block -- install-cyclaw.sh
# and setup-cyclaw-keys.sh's independent marker blocks in the shell rc file.
# The home directory (sessions, venv, repo clone, .env) is KEPT by default
# so no data is lost; pass --remove-home to delete it (prompts first).
# Keychain items are KEPT by default; pass --remove-keychain to delete only
# the five documented CyClaw services (Darwin / test-mode; prompts y/N).
#
# Usage:
#   bash macos/uninstall-cyclaw.sh                # keep ~/.CyClaw data + Keychain
#   bash macos/uninstall-cyclaw.sh --remove-home  # also delete ~/.CyClaw (prompts)
#   bash macos/uninstall-cyclaw.sh --remove-fsconnect  # prompt before deleting ~/CyClaw-FS
#   bash macos/uninstall-cyclaw.sh --remove-keychain   # prompt before Keychain purge
#   bash macos/uninstall-cyclaw.sh --remove-keychain --yes  # non-interactive Keychain purge
#
# --yes / --assume-yes confirms already-requested destructive flags only
# (--remove-home, --remove-keychain, --remove-fsconnect). Default stays safe:
# a missing TTY or empty answer is N. --yes alone deletes nothing extra.
#
# Before rc/home teardown this script best-effort frees loopback listeners on
# CYCLAW_GATE_PORT / CYCLAW_HARNESS_PORT (defaults 8787 / 8790) after LaunchAgent
# bootout, so a later reinstall is not talking to a stale gate/harness. Kill
# failure never aborts uninstall. Duplicated in setup-cyclaw-keys.sh because
# that script is copied standalone to ~/.CyClaw/bin/.

set -euo pipefail

REMOVE_HOME=0
REMOVE_FSCONNECT=0
REMOVE_KEYCHAIN=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --remove-home) REMOVE_HOME=1; shift ;;
    --remove-fsconnect) REMOVE_FSCONNECT=1; shift ;;
    --remove-keychain) REMOVE_KEYCHAIN=1; shift ;;
    --yes|--assume-yes) ASSUME_YES=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

HOME_DIR="$HOME/.CyClaw"
FSCONNECT_DIR="$HOME/CyClaw-FS"
# Same account naming as setup-cyclaw-keys.sh / cyclaw-keychain-set.sh.
ACCOUNT="$(id -un)"
GATE_PORT="${CYCLAW_GATE_PORT:-8787}"
HARNESS_PORT="${CYCLAW_HARNESS_PORT:-8790}"
SECURITY_BIN=""

# Documented Keychain services only — never a wildcard delete. Names match
# setup-cyclaw-keys.sh's KC_* constants.
KC_API="com.cgfixit.cyclaw.api-key"
KC_TELEGRAM="com.cgfixit.cyclaw.telegram-bot-token"
KC_GROK="com.cgfixit.cyclaw.grok-api-key"
KC_ANTHROPIC="com.cgfixit.cyclaw.anthropic-api-key"
KC_GH="com.cgfixit.cyclaw.gh-token"

confirm_destructive() {
  local prompt="$1" answer=""
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  printf '%s (y/N) ' "$prompt"
  read -r answer || true
  case "$answer" in
    y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

# Fail-soft: signal TCP LISTEN pids on $1. Never abort the caller.
# Port-scoped (not a process-name sweep of python). Duplicated in
# setup-cyclaw-keys.sh.
free_loopback_port() {
  local port="$1" pids="" pid=""
  case "$port" in
    ''|*[!0-9]*)
      echo "[cyclaw] WARNING: refusing to free a non-numeric port ('$port')" >&2
      return 0
      ;;
  esac
  if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "[cyclaw] WARNING: port $port out of range; left listeners alone" >&2
    return 0
  fi
  if ! command -v lsof >/dev/null 2>&1; then
    echo "[cyclaw] WARNING: lsof not found; cannot free listeners on :$port" >&2
    return 0
  fi
  # Any bind on this TCP port blocks a later 127.0.0.1 bind (wildcard included).
  pids="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return 0
  fi
  for pid in $pids; do
    case "$pid" in
      ''|*[!0-9]*) continue ;;
    esac
    if [ "$pid" = "$$" ] || [ "$pid" -eq 1 ]; then
      continue
    fi
    echo "[cyclaw] stopping listener pid $pid on :$port"
    kill "$pid" 2>/dev/null || echo "[cyclaw] WARNING: could not signal pid $pid on :$port" >&2
  done
  return 0
}

free_cyclaw_loopback_ports() {
  echo "[cyclaw] freeing loopback listeners on :$GATE_PORT / :$HARNESS_PORT (best-effort)..."
  free_loopback_port "$GATE_PORT"
  free_loopback_port "$HARNESS_PORT"
}

remove_keychain_item() {
  local service="$1" rc=0
  # Never put a secret on argv. delete-generic-password takes account + service.
  # Capture rc via `||` — `if ! cmd; then rc=$?` is 0 in bash (the if succeeded).
  "$SECURITY_BIN" delete-generic-password -a "$ACCOUNT" -s "$service" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[cyclaw] Keychain: deleted $service (account=$ACCOUNT)"
    return 0
  fi
  # 44 = errSecItemNotFound — already gone is success.
  if [ "$rc" -eq 44 ]; then
    echo "[cyclaw] Keychain: $service already absent (account=$ACCOUNT)"
    return 0
  fi
  echo "[cyclaw] WARNING: could not delete Keychain service=$service account=$ACCOUNT (security exit $rc)" >&2
  return 0
}

purge_cyclaw_keychain() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || echo unknown)"
  if [ "$uname_s" != "Darwin" ] && [ "${CYCLAW_UNINSTALL_TEST_MODE:-}" != "1" ]; then
    echo "[cyclaw] --remove-keychain is Darwin-only (macOS Keychain); skipped"
    return 0
  fi
  if [ "${CYCLAW_UNINSTALL_TEST_MODE:-}" = "1" ]; then
    SECURITY_BIN="$(command -v security 2>/dev/null || true)"
  elif [ -x /usr/bin/security ]; then
    SECURITY_BIN="/usr/bin/security"
  else
    SECURITY_BIN="$(command -v security 2>/dev/null || true)"
  fi
  if [ -z "$SECURITY_BIN" ]; then
    echo "[cyclaw] WARNING: security(1) not found; Keychain items were not removed" >&2
    return 0
  fi
  echo "[cyclaw] removing documented CyClaw Keychain items for account=$ACCOUNT..."
  remove_keychain_item "$KC_API"
  remove_keychain_item "$KC_TELEGRAM"
  remove_keychain_item "$KC_GROK"
  remove_keychain_item "$KC_ANTHROPIC"
  remove_keychain_item "$KC_GH"
}

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
# Generators (none of these go through sync.cli): telegram-poll KeepAlive,
# telegram-health, fsconnect-trash, gate/harness (generate_service_plist.py),
# keys-rotate (setup-cyclaw-keys.sh --schedule-rotate), opentweet
# (python -m opentweet.cli schedule-plist). A loaded KeepAlive or crash-restart
# agent would keep running after `cyclaw` is gone. Best-effort: bootout the
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
    com.cgfixit.cyclaw.keys-rotate \
    com.cgfixit.cyclaw.opentweet \
    com.cgfixit.cyclaw.sync
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
# After launchd bootout so a crash-only KeepAlive job is not immediately
# respawned, then signal any leftover invoke-cyclaw / uvicorn listeners.
free_cyclaw_loopback_ports

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
  if confirm_destructive "Delete $HOME_DIR including all sessions and the venv?"; then
    rm -rf "$HOME_DIR"
    echo "[cyclaw] removed $HOME_DIR"
  else
    echo "[cyclaw] kept $HOME_DIR"
  fi
fi

if [ "$REMOVE_HOME" -eq 1 ] && [ "$REMOVE_KEYCHAIN" -eq 0 ]; then
  echo "[cyclaw] NOTE: Keychain items were not removed. A later reinstall can revive"
  echo "[cyclaw]       old CYCLAW_API_KEY / provider tokens from services named"
  echo "[cyclaw]       com.cgfixit.cyclaw.* (account=$ACCOUNT). Re-run with --remove-keychain"
  echo "[cyclaw]       to delete the five documented items, or leave them if you still want them."
fi

if [ "$REMOVE_KEYCHAIN" -eq 1 ]; then
  if confirm_destructive "Delete the five documented CyClaw Keychain items for $ACCOUNT?"; then
    purge_cyclaw_keychain
  else
    echo "[cyclaw] kept Keychain items"
  fi
fi

if [ "$REMOVE_FSCONNECT" -eq 1 ] && { [ -e "$FSCONNECT_DIR" ] || [ -L "$FSCONNECT_DIR" ]; }; then
  if [ -L "$FSCONNECT_DIR" ] || [ "$FSCONNECT_DIR" != "$HOME/CyClaw-FS" ]; then
    echo "[cyclaw] WARNING: refusing unexpected fsconnect target: $FSCONNECT_DIR" >&2
    exit 1
  fi
  if confirm_destructive "Delete $FSCONNECT_DIR and every file in the fsconnect jail?"; then
    rm -rf "$FSCONNECT_DIR"
    echo "[cyclaw] removed $FSCONNECT_DIR (config remains fail-closed until setup is rerun)"
  else
    echo "[cyclaw] kept $FSCONNECT_DIR"
  fi
elif [ "$REMOVE_FSCONNECT" -eq 0 ] && [ -d "$FSCONNECT_DIR" ]; then
  echo "[cyclaw] kept $FSCONNECT_DIR (pass --remove-fsconnect to remove it)"
fi

echo "[cyclaw] uninstall complete."
