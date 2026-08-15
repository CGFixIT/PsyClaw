#!/usr/bin/env bash
# Fetch one secret from the macOS Keychain and inject it into a named
# environment variable before exec'ing the wrapped command. Generated
# launchd plists (python -m telegram.cli poll-plist / health-plist,
# macos/generate_service_plist.py) put this in front of ProgramArguments
# instead of ever writing a token into the plist itself.
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
#   macos/cyclaw-keychain-set.sh <keychain-service>
# (interactive, no-echo prompt; never puts the secret in any process's argv --
# see that script's own header for how.)
#
# Fails closed: exits 1 without exec'ing anything if the Keychain item is
# missing or empty, rather than launching the wrapped process with the
# variable silently unset.
#
# ACL / trust caveat (real, not fixable from this script -- corrected
# 2026-08-15, see PR review history: an earlier version of this comment
# claimed the "Always Allow" grant went to /bin/bash the interpreter; that
# was never verified against actual Keychain behavior and is very likely
# wrong): this script never calls a Keychain API itself -- it execs
# /usr/bin/security as a child process, and that child is what actually
# calls SecKeychainFindGenericPassword. macOS Keychain ACL trust decisions
# are attributed to the code signature/path of the DIRECT caller of the
# Security framework API, so the "Always Allow" grant on first read is
# attributed to /usr/bin/security (a stable, Apple-signed system binary),
# NOT to /bin/bash and NOT to this script specifically. Practical
# consequence: "Always Allow" grants read access to ANY local process able
# to invoke `security find-generic-password -a <account> -s <service> -w`
# for that service name -- broader than "just this wrapper," but also more
# specific than "all of bash." cyclaw-keychain-set.sh pins the trusted
# reader explicitly at store time (-T /usr/bin/security) rather than
# relying on default creator-trust plus an interactive "Always Allow"
# choice, which narrows this to "processes that can run /usr/bin/security"
# instead of leaving it to whatever the operator clicks on first read.
# VERIFY ON REAL HARDWARE: this attribution has not been confirmed against
# an actual Keychain Access ACL entry or "Allow"/"Always Allow" dialog --
# check what application name/path the prompt shows, and inspect the
# stored item's access-control-list (Keychain Access.app -> item -> Access
# Control, or `security dump-keychain`) after first use. A fully narrow
# ACL (trusted only to a specific unsigned script) is not achievable this
# way at all -- Keychain trusts code signatures/paths of binaries, not
# shell scripts; a signed helper binary is the only way to get an ACL
# pinned to "this wrapper and nothing else."

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
