#!/usr/bin/env bash
# otel-hardening verify — clean-tree pass + mutation self-test.
# Pure stdlib (tomllib); safe to run before any pip install. Exit 0 = healthy.
# A checker that cannot fail proves nothing — every rule below is exercised by
# a mutation that must flip it, and each mutation asserts it actually changed
# the file first (two prior silent-no-op sed bugs are documented in git
# history at the old T3/T5 scenarios; the assert-changed discipline is why).
# No test here touches the network.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/check_otel.py"

# The date the shipped contract was last reviewed. Used to make the strict
# clean-tree run deterministic forever; the non-strict run uses today so real
# staleness still surfaces (as WARN, exit 0) without breaking this script.
AS_OF="2026-08-27"

echo "== otel-hardening verify =="

# 1. Clean tree, default severity, live date: must exit 0.
if python3 "$checker" --repo-root "$repo_root" >/tmp/otelguard_live.txt 2>&1; then
  echo "clean tree (default): PASS (exit 0)"
else
  echo "clean tree (default): FAIL — the shipped kill-switch contract is broken" >&2
  cat /tmp/otelguard_live.txt >&2
  exit 1
fi

# 2. Clean tree, --strict, pinned date: zero warnings allowed at the review date.
if python3 "$checker" --repo-root "$repo_root" --strict --as-of "$AS_OF" >/tmp/otelguard_strict.txt 2>&1; then
  echo "clean tree (--strict @ $AS_OF): PASS (exit 0)"
else
  echo "clean tree (--strict @ $AS_OF): FAIL — a WARN-class regression landed" >&2
  cat /tmp/otelguard_strict.txt >&2
  exit 1
fi

# Fresh temp repo carrying everything the checker reads (a partial tree would
# fail checks for the wrong reason and mask what a mutation actually proved).
_mktree() {
  local d; d="$(mktemp -d)"
  mkdir -p "$d/utils" "$d/retrieval" "$d/guardrails" "$d/docs/security-philosophy" \
           "$d/macos" "$d/powershell" "$d/windows" "$d/agentic/executor" \
           "$d/agentic/fsconnect" "$d/sync" "$d/telegram" "$d/opentweet"
  cp "$repo_root/pyproject.toml" "$repo_root/constraints.txt" "$repo_root/requirements.txt" \
     "$repo_root/environment.yml" "$repo_root/Dockerfile" "$repo_root/docker-compose.yml" "$d/"
  cp "$repo_root/utils/telemetry_kill.py" "$repo_root/utils/onnx_telemetry.py" "$d/utils/"
  cp "$repo_root/retrieval/embeddings.py" "$repo_root/retrieval/vector_store.py" "$d/retrieval/"
  cp "$repo_root/guardrails/integration.py" "$d/guardrails/"
  cp "$repo_root/docs/security-philosophy/cyclaw_telemetry_kill.env" "$d/docs/security-philosophy/"
  cp "$repo_root/macos/invoke-cyclaw.sh" "$repo_root/macos/generate_service_plist.py" "$d/macos/"
  cp "$repo_root/powershell/Invoke-CyClaw.ps1" "$repo_root/powershell/Install-CyClaw.ps1" "$d/powershell/"
  cp "$repo_root/windows/generate_service_task.py" "$d/windows/"
  cp "$repo_root/agentic/executor/runner.py" "$d/agentic/executor/"
  cp "$repo_root/agentic/gh_client.py" "$repo_root/agentic/writer.py" "$d/agentic/"
  cp "$repo_root/agentic/fsconnect/cli.py" "$d/agentic/fsconnect/"
  cp "$repo_root/sync/cli.py" "$repo_root/sync/scheduler.py" "$d/sync/"
  cp "$repo_root/telegram/cli.py" "$d/telegram/"
  cp "$repo_root/opentweet/cli.py" "$d/opentweet/"
  echo "$d"
}

fails=0

# run_mutation NAME EXPECT_RC GREP_PATTERN [--strict]
# The temp tree is prepared by the caller in $a; cleans it up afterwards.
_expect() {
  local name="$1" expect_rc="$2" pattern="$3" extra="${4:-}"
  local out rc
  out="$(python3 "$checker" --repo-root "$a" --as-of "$AS_OF" $extra 2>&1)"; rc=$?
  if [ "$rc" -eq "$expect_rc" ] && echo "$out" | grep -q "$pattern"; then
    echo "$name: PASS"
  else
    echo "$name: FAIL — expected rc=$expect_rc + /$pattern/, got rc=$rc" >&2
    echo "$out" >&2
    fails=$((fails + 1))
  fi
  rm -rf "$a"
}

# _mutate FILE PYTHON_SNIPPET — applies the snippet and asserts it changed the file.
_mutate() {
  local file="$1" snippet="$2"
  python3 - "$file" <<PYEOF
import sys
path = sys.argv[1]
before = open(path, encoding="utf-8").read()
text = before
$snippet
assert text != before, "mutation was a silent no-op -- update this scenario"
open(path, "w", encoding="utf-8").write(text)
PYEOF
}

# 3. T1: empty the TELEMETRY_KILL dict entirely.
a="$(_mktree)"
_mutate "$a/utils/telemetry_kill.py" '
import re
text = re.sub(r"TELEMETRY_KILL: dict\[str, str\] = \{.*?\n\}", "TELEMETRY_KILL: dict[str, str] = {}", text, count=1, flags=re.S)'
_expect "T1 empty-dict mutation" 2 "FAIL  \[T1\]"

# 4. T2: VALUE flip — OTEL_SDK_DISABLED true -> false (key survives; only the
#    independent value oracle can catch this).
a="$(_mktree)"
_mutate "$a/utils/telemetry_kill.py" '
text = text.replace("\"OTEL_SDK_DISABLED\": \"true\"", "\"OTEL_SDK_DISABLED\": \"false\"", 1)'
_expect "T2 value-flip (SDK re-enabled) mutation" 2 "FAIL  \[T2\]"

# 5. T2: VALUE flip — exporter none -> otlp.
a="$(_mktree)"
_mutate "$a/utils/telemetry_kill.py" '
text = text.replace("\"OTEL_TRACES_EXPORTER\": \"none\"", "\"OTEL_TRACES_EXPORTER\": \"otlp\"", 1)'
_expect "T2 exporter none->otlp mutation" 2 "FAIL  \[T2\]"

# 6. T2: dropped key (ANONYMIZED_TELEMETRY).
a="$(_mktree)"
_mutate "$a/utils/telemetry_kill.py" '
text = text.replace("    \"ANONYMIZED_TELEMETRY\": \"False\",\n", "", 1)'
_expect "T2 dropped-key mutation" 2 "FAIL  \[T2\]"

# 7. T2: update-check value flip 1 -> 0.
a="$(_mktree)"
_mutate "$a/utils/telemetry_kill.py" '
text = text.replace("\"PIP_DISABLE_PIP_VERSION_CHECK\": \"1\"", "\"PIP_DISABLE_PIP_VERSION_CHECK\": \"0\"", 1)'
_expect "T2 update-check 1->0 mutation" 2 "FAIL  \[T2\]"

# 8. T3: scrub-name deletion (declarative OTel config pointer survives).
a="$(_mktree)"
_mutate "$a/utils/telemetry_kill.py" '
text = text.replace("    \"OTEL_CONFIG_FILE\",\n", "", 1)'
_expect "T3 dropped-scrub-name mutation" 2 "FAIL  \[T3\]"

# 9. T4 staleness via injected future date: WARN by default, exit 2 under --strict.
a="$(_mktree)"
out="$(python3 "$checker" --repo-root "$a" --as-of 2027-12-31 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q "WARN  \[T4\]"; then
  echo "T4 future --as-of (default): PASS (WARN, exit 0)"
else
  echo "T4 future --as-of (default): FAIL — expected WARN/exit 0, got rc=$rc" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
out="$(python3 "$checker" --repo-root "$a" --as-of 2027-12-31 --strict 2>&1)"; rc=$?
if [ "$rc" -eq 2 ]; then
  echo "T4 future --as-of (--strict): PASS (escalated to exit 2)"
else
  echo "T4 future --as-of (--strict): FAIL — expected exit 2, got rc=$rc" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 10. T5: vendor pin drift (pattern-matched bump, never a hardcoded literal).
a="$(_mktree)"
_mutate "$a/pyproject.toml" '
import re
text, n = re.subn(r"langgraph==[0-9]+\.[0-9]+\.[0-9]+", "langgraph==1.9.9", text)
assert n >= 1'
_expect "T5 vendor-pin-drift mutation" 0 "WARN  \[T5\]"

# 11. T7: conditional-offline wiring renamed away.
a="$(_mktree)"
_mutate "$a/retrieval/embeddings.py" '
text = text.replace("_model_offline_eligible", "_model_offline_check_removed")'
_expect "T7 missing-conditional-wiring mutation" 2 "FAIL  \[T7\]"

# 12. T8: reference-env VALUE drift (export survives, value lies).
a="$(_mktree)"
_mutate "$a/docs/security-philosophy/cyclaw_telemetry_kill.env" '
text = text.replace("export OTEL_SDK_DISABLED=true", "export OTEL_SDK_DISABLED=false", 1)'
_expect "T8 env value-drift mutation" 2 "FAIL  \[T8\]"

# 13. T8: dropped `export` keyword (a bare KEY=value never reaches a child).
a="$(_mktree)"
_mutate "$a/docs/security-philosophy/cyclaw_telemetry_kill.env" '
text = text.replace("export DO_NOT_TRACK=1", "DO_NOT_TRACK=1", 1)'
_expect "T8 missing-export mutation" 2 "FAIL  \[T8\]"

# 14. T8: duplicate line.
a="$(_mktree)"
_mutate "$a/docs/security-philosophy/cyclaw_telemetry_kill.env" '
text = text + "export DO_NOT_TRACK=1\n"'
_expect "T8 duplicate-line mutation" 2 "FAIL  \[T8\]"

# 15. T10: Dockerfile delivery drift (GH_TELEMETRY flipped to true).
a="$(_mktree)"
_mutate "$a/Dockerfile" '
text = text.replace("GH_TELEMETRY=false", "GH_TELEMETRY=true", 1)'
_expect "T10 Dockerfile value-flip mutation" 2 "FAIL  \[T10\]"

# 16. T11: launcher stops exporting the canonical block.
a="$(_mktree)"
_mutate "$a/macos/invoke-cyclaw.sh" '
text = text.replace("-m utils.telemetry_kill --export shell", "-m utils.telemetry_kill --export-DISABLED", 1)'
_expect "T11 launcher-export-removed mutation" 2 "FAIL  \[T11\]"

# 17. T12: a runtime file grows a programmatic re-enable call.
a="$(_mktree)"
_mutate "$a/retrieval/vector_store.py" '
text = text + "\n\ndef _oops():\n    import onnxruntime\n    onnxruntime.enable_telemetry_events()\n"'
_expect "T12 programmatic-bypass mutation" 2 "FAIL  \[T12\]"

# 18. T12: a child-env builder drops the canonical map.
a="$(_mktree)"
_mutate "$a/agentic/gh_client.py" '
text = text.replace("build_telemetry_safe_env", "plain_environ_passthrough")'
_expect "T12 dropped-builder mutation" 2 "FAIL  \[T12\]"

# 19. T13: unclassified new dependency — WARN default, FAIL under --strict.
a="$(_mktree)"
_mutate "$a/requirements.txt" '
text = text + "\nsome-new-vendor==1.0.0\n"'
out="$(python3 "$checker" --repo-root "$a" --as-of "$AS_OF" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q "WARN  \[T13\].*some-new-vendor"; then
  echo "T13 unclassified-dep (default): PASS (WARN, exit 0)"
else
  echo "T13 unclassified-dep (default): FAIL — expected WARN/exit 0, got rc=$rc" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
out="$(python3 "$checker" --repo-root "$a" --as-of "$AS_OF" --strict 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q "FAIL  \[T13\].*some-new-vendor"; then
  echo "T13 unclassified-dep (--strict): PASS (FAIL, exit 2)"
else
  echo "T13 unclassified-dep (--strict): FAIL — expected exit 2, got rc=$rc" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 20. T13: a category-3 row inventing a control must fail schema validation.
a="$(_mktree)"
cp "$checker" "$a/check_otel_mutated.py"
_mutate "$a/check_otel_mutated.py" '
old = "\"name\": \"telegram channel\", \"category\": 3, \"controls\": {},"
assert old in text
text = text.replace(old, "\"name\": \"telegram channel\", \"category\": 3, \"controls\": {\"OTEL_SDK_DISABLED\": \"true\"},", 1)'
out="$(python3 "$a/check_otel_mutated.py" --repo-root "$a" --as-of "$AS_OF" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q "FAIL  \[T13\].*must not invent controls"; then
  echo "T13 invented-control mutation: PASS"
else
  echo "T13 invented-control mutation: FAIL — expected exit 2, got rc=$rc" >&2
  echo "$out" >&2
  fails=$((fails + 1))
fi
rm -rf "$a"

# 21. T14: an ONNX load seam drops the API call. The replacement must NOT
#     contain the original as a substring, or the checker's needle still
#     matches and the mutation proves nothing (found the hard way).
a="$(_mktree)"
_mutate "$a/guardrails/integration.py" '
text = text.replace("suppress_onnx_telemetry", "zz_onnx_call_removed_zz")'
_expect "T14 dropped-seam mutation" 2 "FAIL  \[T14\]"

# 22. T13: the ONNX floor pin is lowered below the release where
#     ORT_DISABLE_TELEMETRY starts governing the non-Windows 1DS path. Both
#     required surfaces must go red, not just report an info line.
a="$(_mktree)"
_mutate "$a/constraints.txt" '
text = text.replace("onnxruntime==1.29.0", "onnxruntime==1.28.0")'
_mutate "$a/pyproject.toml" '
text = text.replace("onnxruntime==1.29.0", "onnxruntime==1.28.0")'
_expect "T13 ONNX floor lowered mutation" 2 "FAIL  \[T13\].*below the 1.29.0 floor"

# 23. T13: the pins are deleted outright, as a future dependency edit might do.
#     A missing floor on a REQUIRED surface must fail -- reporting it with
#     info() left the checker silent while both shipped surfaces could resolve
#     below the floor.
a="$(_mktree)"
_mutate "$a/constraints.txt" '
import re
text = re.sub(r"(?m)^onnxruntime==[^\n]*\n", "", text)'
_mutate "$a/pyproject.toml" '
import re
text = re.sub(r"(?m)^\s*\"onnxruntime==[^\n]*\n", "", text)'
_expect "T13 ONNX pins deleted mutation" 2 "FAIL  \[T13\].*no onnxruntime floor"

# 24. T13: the floor check itself is neutered. Without this scenario a
#     regression could delete the enforcement while this verifier stayed green
#     -- the mutations above only prove the check works when it is called.
#     The replacement must not contain the original body as a substring.
a="$(_mktree)"
mkdir -p "$a/.claude/skills/otel-hardening"
cp "$checker" "$a/.claude/skills/otel-hardening/check_otel.py"
if ! _mutate "$a/.claude/skills/otel-hardening/check_otel.py" '
text = text.replace(
    "    for surface, pinned in _pins_by_surface(root, \"onnxruntime\").items():\n"
    "        _ort_floor_verdict(surface, pinned)",
    "    return  # zz_ort_floor_check_removed_zz")'; then
  # The mutation not applying means the body it targets is already gone or
  # renamed. Reporting PASS there would be passing for the wrong reason -- the
  # scenario would be silently guarding nothing.
  echo "T13 ONNX floor-check removed mutation: FAIL — mutation did not apply; update this scenario" >&2
  fails=$((fails + 1))
else
  out="$(python3 "$a/.claude/skills/otel-hardening/check_otel.py" --repo-root "$a" --as-of "$AS_OF" 2>&1)"
  if echo "$out" | grep -q "onnxruntime pinned"; then
    echo "T13 ONNX floor-check removed mutation: FAIL — neutered check still reported a verdict" >&2
    fails=$((fails + 1))
  else
    echo "T13 ONNX floor-check removed mutation: PASS"
  fi
fi
rm -rf "$a"

echo
if [ "$fails" -eq 0 ]; then
  echo "otel-hardening verify: ALL PASS"
  exit 0
else
  echo "otel-hardening verify: $fails mutation test(s) FAILED" >&2
  exit 1
fi
