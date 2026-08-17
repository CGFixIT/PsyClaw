#!/usr/bin/env bash
# Local PR-body gate against .github/PULL_REQUEST_TEMPLATE.md minimums.
#
# Usage:
#   scripts/check-pr-template.sh PATH/TO/body.md
#   gh pr view --json body -q .body | scripts/check-pr-template.sh -
#   CYCLAW_PR_BODY_FILE=body.md scripts/check-pr-template.sh
#
# Exit 0 = ok; exit 1 = missing required sections.
# Git hooks cannot intercept GitHub API / gh pr create bodies — agents and
# humans should run this before opening a PR. CI runs the same headers as a
# blocking check (.github/workflows/pr-template-check.yml).
set -euo pipefail

input="${1:-${CYCLAW_PR_BODY_FILE:-}}"
if [[ -z "$input" ]]; then
  printf '%s\n' \
    "usage: scripts/check-pr-template.sh <body.md|->" \
    "   or: CYCLAW_PR_BODY_FILE=body.md scripts/check-pr-template.sh" \
    >&2
  exit 2
fi

if [[ "$input" == "-" ]]; then
  body="$(cat)"
elif [[ -f "$input" ]]; then
  body="$(cat "$input")"
else
  printf 'check-pr-template: file not found: %s\n' "$input" >&2
  exit 2
fi

fail=0
missing=()

require_header() {
  local label="$1"
  local pattern="$2"
  if ! printf '%s' "$body" | grep -Eiq "$pattern"; then
    missing+=("$label")
    fail=1
  fi
}

# Align with template + advisory CI loose matching, but require the CyClaw-
# named sections that Grok Build / agents must fill.
require_header "Proposed changes (or Why/Benefits/Summary)" \
  '^#{1,4}[[:space:]]*(proposed changes|benefits|why|summary|what)\b'
require_header "Types of changes" \
  '^#{1,4}[[:space:]]*types of changes\b'
require_header "Benefits / why" \
  '^#{1,4}[[:space:]]*(benefits|why)\b'
require_header "Risks to monitor" \
  '^#{1,4}[[:space:]]*risks?([[:space:]]*(to[[:space:]]*monitor|impact))?\b'
require_header "Checklist" \
  '^#{1,4}[[:space:]]*checklist\b'

if [[ "${#body}" -lt 40 ]]; then
  missing+=("Body too short (< 40 chars)")
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  printf '%s\n' \
    "check-pr-template: PR body is missing required sections from" \
    "  .github/PULL_REQUEST_TEMPLATE.md" \
    "" \
    "Missing:" \
    >&2
  for m in "${missing[@]}"; do
    printf '  - %s\n' "$m" >&2
  done
  printf '%s\n' \
    "" \
    "Fill the full template before: gh pr create / GitHub connector create_pull_request" \
    "Grok Build branch prefix: grok/<feature>" \
    "Title format: [prefix] - Short descriptive sentence" \
    >&2
  exit 1
fi

printf 'check-pr-template: OK — required template sections present\n'
exit 0
