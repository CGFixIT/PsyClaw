#!/usr/bin/env bash
# OTel-Hardening verify — clean-tree pass + mutation self-test.
# Pure stdlib (tomllib); safe to run before any pip install. Exit 0 = healthy.
# A checker that cannot fail proves nothing — the mutation tests keep it honest.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/check_otel.py"

echo "== OTel-Hardening verify =="

# 1. Clean tree must pass (exit 0, 0 failures).
if python3 "$checker" --repo-root "$repo_root" >/tmp/otelguard_live.txt 2>&1; then
  echo "clean tree: PASS (exit 0)"
else
  echo "clean tree: FAIL — the shipped kill-switch contract is broken" >&2
  cat /tmp/otelguard_live.txt >&2
  exit 1
fi

# Helper: fresh temp repo carrying only what the checker reads.
_mktree() {
  local d; d="$(mktemp -d)"
  mkdir -p "$d/utils" "$d/retrieval" "$d/docs/security-philosophy"
  cp "$repo_root/pyproject.toml" "$d/"
  cp "$repo_root/utils/telemetry_kill.py" "$d/utils/"
  cp "$repo_root/retrieval/embeddings.py" "$d/retrieval/"
  cp "$repo_root/docs/security-philosophy/cyclaw_telemetry_kill.env" "$d/docs/security-philosophy/"
  echo "$d"
}

fails=0

# 2. T1 FAIL: empty the TELEMETRY_KILL dict.
a="$(_mktree)"
python3 - "$a/utils/telemetry_kill.py" <<'PYEOF'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
text = re.sub(r"TELEMETRY_KILL: dict\[str, str\] = \{.*?\n\}", "TELEMETRY_KILL: dict[str, str] = {}", text, count=1, flags=re.S)
open(path, "w", encoding="utf-8").write(text)
PYEOF
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q "FAIL  \[T1\]"; then
  echo "T1 empty-dict mutation: PASS (correctly FAILed)"
else
  echo "T1 empty-dict mutation: FAIL — checker did not catch it (rc=$rc)" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 3. T2 FAIL: drop a baseline key (ANONYMIZED_TELEMETRY).
a="$(_mktree)"
sed -i.bak '/"ANONYMIZED_TELEMETRY": "False",/d' "$a/utils/telemetry_kill.py"
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q "FAIL  \[T2\]"; then
  echo "T2 dropped-key mutation: PASS (correctly FAILed)"
else
  echo "T2 dropped-key mutation: FAIL — checker did not catch it (rc=$rc)" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 4. T3 FAIL: shrink _TRACING_CREDENTIALS to two names.
a="$(_mktree)"
sed -i.bak 's/_TRACING_CREDENTIALS = ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT")/_TRACING_CREDENTIALS = ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY")/' "$a/utils/telemetry_kill.py"
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q "FAIL  \[T3\]"; then
  echo "T3 shrunk-credentials mutation: PASS (correctly FAILed)"
else
  echo "T3 shrunk-credentials mutation: FAIL — checker did not catch it (rc=$rc)" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 5. T7 FAIL: rename _model_offline_eligible out of embeddings.py (simulates a
#    regression that silently drops the HF Hub half of the kill-switch story).
a="$(_mktree)"
sed -i.bak 's/_model_offline_eligible/_model_offline_check_removed/g' "$a/retrieval/embeddings.py"
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q "FAIL  \[T7\]"; then
  echo "T7 missing-conditional-wiring mutation: PASS (correctly FAILed)"
else
  echo "T7 missing-conditional-wiring mutation: FAIL — checker did not catch it (rc=$rc)" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 6. T8 WARN (not FAIL) + --strict escalation: drop one line from the reference .env.
a="$(_mktree)"
sed -i.bak '/^ANONYMIZED_TELEMETRY=False$/d' "$a/docs/security-philosophy/cyclaw_telemetry_kill.env"
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q "WARN  \[T8\]"; then
  echo "T8 doc-drift mutation (default): PASS (WARN, exit 0)"
else
  echo "T8 doc-drift mutation (default): FAIL — expected WARN/exit 0, got rc=$rc" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
strict_out="$(python3 "$checker" --repo-root "$a" --strict 2>&1)"; strict_rc=$?
if [ "$strict_rc" -eq 2 ]; then
  echo "T8 doc-drift mutation (--strict): PASS (escalated to exit 2)"
else
  echo "T8 doc-drift mutation (--strict): FAIL — expected exit 2, got rc=$strict_rc" >&2
  echo "$strict_out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 7. T5 WARN: bump a tracked vendor pin past the recorded baseline in a temp pyproject.toml.
a="$(_mktree)"
sed -i.bak 's/langgraph==1\.2\.6/langgraph==1.9.9/' "$a/pyproject.toml"
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q "WARN  \[T5\]"; then
  echo "T5 vendor-pin-drift mutation: PASS (WARN, exit 0)"
else
  echo "T5 vendor-pin-drift mutation: FAIL — expected WARN/exit 0, got rc=$rc" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

echo
if [ "$fails" -eq 0 ]; then
  echo "OTel-Hardening verify: ALL PASS"
  exit 0
else
  echo "OTel-Hardening verify: $fails mutation test(s) FAILED" >&2
  exit 1
fi
