#!/usr/bin/env bash
# Store one secret in the macOS Keychain for cyclaw-keychain-env.sh to read
# back at launchd-job start time.
#
# Usage: cyclaw-keychain-set.sh <keychain-service>
#
# Prompts for the secret with echo off (never accepts it as an argv value,
# which would leak into shell history and be visible to any local user via
# `ps`). Re-running with the same service name updates the stored value
# (security ... -U).

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: cyclaw-keychain-set.sh <keychain-service>" >&2
  exit 1
fi

SERVICE="$1"

printf 'Secret for Keychain service "%s": ' "$SERVICE"
read -r -s SECRET
printf '\n'

if [ -z "$SECRET" ]; then
  echo "cyclaw-keychain-set: empty secret refused" >&2
  exit 1
fi

ACCOUNT="$(id -un)"
security add-generic-password -a "$ACCOUNT" -s "$SERVICE" -w "$SECRET" -U
echo "[cyclaw] stored Keychain item: service=$SERVICE account=$ACCOUNT"
