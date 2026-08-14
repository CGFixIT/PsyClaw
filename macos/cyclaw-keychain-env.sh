#!/usr/bin/env bash
# Fetch one secret from the macOS Keychain and inject it into a named
# environment variable before exec'ing the wrapped command. Generated
# launchd plists (python -m telegram.cli poll-plist / health-plist) put
# this in front of ProgramArguments instead of ever writing a token into
# the plist itself.
#
# Usage:
#   cyclaw-keychain-env.sh <keychain-service> <env-var-name> -- <command> [args...]
#
# Composable: chain multiple invocations to inject multiple secrets, e.g.
#   cyclaw-keychain-env.sh svc-a VAR_A -- \
#   cyclaw-keychain-env.sh svc-b VAR_B -- \
#   python -m telegram.cli poll
# Each layer exports its one variable, then execs into the next layer (or
# the real command on the last one) -- exec never resets the environment,
# so every variable exported by an earlier layer is still set downstream.
#
# Store a secret first with:
#   security add-generic-password -a "$(id -un)" -s <keychain-service> -w '<secret>' -U
# (cyclaw-keychain-set.sh in this directory wraps that with a no-echo prompt.)
#
# Fails closed: exits 1 without exec'ing anything if the Keychain item is
# missing or empty, rather than launching the wrapped process with the
# variable silently unset.
#
# ACL caveat (real, not fixable from this script): this is a bash script,
# not a signed binary, so the first "Always Allow" prompt macOS shows when
# `security find-generic-password -w` reads a protected item is typically
# attributed to the interpreter (/bin/bash) rather than to this specific
# script -- meaning "Always Allow" can grant read access to any later bash
# process run by the same user, not just this wrapper. This is a general
# macOS Keychain limitation for interpreted/unsigned callers, not something
# CyClaw's code controls. Verify the actual prompt/ACL behavior on real
# hardware before deciding whether this trust boundary is acceptable for a
# given deployment; a signed binary or a narrower ACL would be the way to
# tighten it later if needed.

set -euo pipefail

if [ "$#" -lt 4 ] || [ "$3" != "--" ]; then
  echo "usage: cyclaw-keychain-env.sh <keychain-service> <env-var-name> -- <command> [args...]" >&2
  exit 1
fi

SERVICE="$1"
VAR_NAME="$2"
shift 3

if ! [[ "$VAR_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "cyclaw-keychain-env: refusing invalid environment variable name: $VAR_NAME" >&2
  exit 1
fi

ACCOUNT="$(id -un)"
SECRET="$(security find-generic-password -a "$ACCOUNT" -s "$SERVICE" -w 2>/dev/null)" || {
  echo "cyclaw-keychain-env: no Keychain item for service '$SERVICE' (account '$ACCOUNT')" >&2
  echo "cyclaw-keychain-env: store it first: macos/cyclaw-keychain-set.sh '$SERVICE'" >&2
  exit 1
}

if [ -z "$SECRET" ]; then
  echo "cyclaw-keychain-env: Keychain item for service '$SERVICE' is empty" >&2
  exit 1
fi

export "$VAR_NAME=$SECRET"
exec "$@"
