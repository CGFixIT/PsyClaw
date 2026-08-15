#!/usr/bin/env bash
# Store one secret in the macOS Keychain for cyclaw-keychain-env.sh to read
# back at launchd-job start time.
#
# Usage: cyclaw-keychain-set.sh <keychain-service>
#
# Does NOT read the secret into this script's own variables or argv. `security
# add-generic-password`'s -w flag takes an OPTIONAL attached value (getopt
# "w::" style: only a value concatenated directly onto -w, e.g. -wfoo, counts
# as supplied) -- called here as a bare `-w` with no attached value, which
# makes `security` itself prompt for the password via a secure TTY read
# (readpassphrase(3), no echo) and pass it straight into the Keychain item.
# That means the secret is NEVER a shell variable in this script and NEVER an
# argv token of the `security` child process, closing the `ps`/procfs exposure
# window a `read` + `-w "$SECRET"` pipeline would otherwise have.
#
# VERIFY ON REAL HARDWARE (documented per this repo's convention for
# macOS-CLI-specific behavior that cannot be exercised outside Darwin, matching
# the ACL caveat in cyclaw-keychain-env.sh): confirm that a bare trailing `-w`
# actually triggers the interactive TTY prompt on your macOS/security version,
# rather than being silently swallowed or requiring `-U` to appear first, and
# that `ps`/`ps -ww`/`/proc`-equivalent inspection during the prompt shows no
# secret in this process's or the `security` child's argv.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: cyclaw-keychain-set.sh <keychain-service>" >&2
  exit 1
fi

if [ ! -t 0 ]; then
  echo "cyclaw-keychain-set: requires an interactive terminal (security needs a TTY to prompt)" >&2
  exit 1
fi

SERVICE="$1"
ACCOUNT="$(id -un)"

echo "[cyclaw] Storing Keychain service '$SERVICE' for account '$ACCOUNT'."
echo "[cyclaw] You will be prompted by 'security' for the secret value (input is not echoed)."
security add-generic-password -a "$ACCOUNT" -s "$SERVICE" -U -w
echo "[cyclaw] stored Keychain item: service=$SERVICE account=$ACCOUNT"
