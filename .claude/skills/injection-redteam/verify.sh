#!/usr/bin/env bash
# injection-redteam verify — baseline pass + regression-detection self-test.
# Requires the project venv (PyYAML + utils importable). Exit 0 = healthy.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
runner="$here/redteam.py"

echo "== injection-redteam verify =="

# Dependency preflight: this skill needs the real sanitizer + PyYAML. If they
# aren't importable (fresh container, deps not installed), skip cleanly rather
# than fail — the CI verify-skills leg is non-blocking and may run pre-install.
if ! python3 -c "import yaml, sys; sys.path.insert(0, '$repo_root'); import utils.sanitizer" 2>/dev/null; then
  echo "SKIP: project deps not importable (need PyYAML + utils on path). Install first." >&2
  exit 0
fi

# 1. Baseline: shipped config, shipped corpus. Exit 0 = no new bypasses / FPs.
if python3 "$runner" >/tmp/redteam_baseline.txt 2>&1; then
  echo "baseline: PASS (no new bypasses, no false positives)"
else
  echo "baseline: FAIL — a NEW bypass or false positive appeared:" >&2
  cat /tmp/redteam_baseline.txt >&2
  exit 1
fi

# 2. Regression self-test: point the runner at a config whose filter is
#    DISABLED. Every 'expect: blocked' anchor should then get through as a NEW
#    (unflagged) bypass, so the runner must exit 2. Proves the harness actually
#    detects a broken sanitizer rather than rubber-stamping.
tmp_cfg="$(mktemp --suffix=.yaml)"
trap 'rm -f "$tmp_cfg"' EXIT
python3 - "$repo_root/config.yaml" "$tmp_cfg" <<'PY'
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(src))
cfg["policy"]["prompt_filter"]["enabled"] = False
yaml.safe_dump(cfg, open(dst, "w"))
PY

if python3 "$runner" --config "$tmp_cfg" >/dev/null 2>&1; then
  echo "regression test: FAIL — disabled filter should surface new bypasses (exit 2)" >&2
  exit 1
fi
echo "regression test: PASS (disabled filter detected as new bypasses, exit 2)"

echo "== injection-redteam verify: OK =="
