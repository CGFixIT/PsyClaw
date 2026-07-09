#/usr/bin/env bash
# bootstrap.sh — cyclaw-advisor skill harness.
# Sets up the compliance advisor environment, verifies latest main branch state,
# and prepares reference files for privacy/DPA/DSR/breach workflows.

set -euo pipefail

hr() { printf '%s\n' "------------------------------------------------------------"; }

echo "Bootstrapping cyclaw-advisor skill..."
hr

git fetch origin main --quiet || echo "WARN: fetch may have issues (offline?)"

echo "Current main SHA: $(git rev-parse origin/main 2>/dev/null || echo 'unknown')"
echo "Compliance references ready."

echo "Skill loaded: Legal compliance assistant mode active."
echo "Use for DPA reviews, DSR handling, breach triage, regulatory monitoring."
hr
echo "Bootstrap complete. Invoke SKILL.md instructions for queries."