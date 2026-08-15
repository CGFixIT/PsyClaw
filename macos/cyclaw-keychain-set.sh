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
# Trust: -T /usr/bin/security pins the item's trusted-application list to the
# actual direct caller of the Keychain API (see cyclaw-keychain-env.sh's own
# header for why that's /usr/bin/security and not /bin/bash), instead of
# leaving the default creator-trust-plus-interactive-prompt behavior, which
# would ask the operator to decide "Allow"/"Always Allow" on first read with
# no guidance. This is deliberately narrower than -A (trust any application --
# never use that here) but cannot be narrowed to "only this specific unsigned
# script," since Keychain ACLs trust code signatures/paths of binaries, not
# shell scripts (/usr/bin/security is what gets trusted either way).
#
# VERIFY ON REAL HARDWARE (documented per this repo's convention for
# macOS-CLI-specific behavior that cannot be exercised outside Darwin, matching
# the ACL caveat in cyclaw-keychain-env.sh): confirm that a bare trailing `-w`
# actually triggers the interactive TTY prompt on your macOS/security version
# rather than being silently swallowed or requiring `-U` to appear first; that
# `ps`/`ps -ww`/`/proc`-equivalent inspection during the prompt shows no secret
# in this process's or the `security` child's argv; and that `-T
# /usr/bin/security` actually suppresses the interactive access prompt on a
# subsequent non-interactive `security find-generic-password -w` read (e.g.
# from a launchd job) -- including on a -U update of an item that already
# exists from before this script carried -T, in case existing trust settings
# on a pre-existing item aren't replaced by an update the way a fresh add is.

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
security add-generic-password -a "$ACCOUNT" -s "$SERVICE" -T /usr/bin/security -U -w
echo "[cyclaw] stored Keychain item: service=$SERVICE account=$ACCOUNT"
