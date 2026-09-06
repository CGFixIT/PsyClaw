#!/usr/bin/env bash
# cyclaw-gotchas verify -- stdlib/shell only, no project deps, runs pre-install.
# Fails only if the skill's own artefacts are broken: driver.sh does not parse,
# the frontmatter name no longer matches the directory, a repo path the skill
# names no longer exists, or `driver.sh inventory` (the one subcommand that
# needs nothing installed) cannot run.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
fail=0
ok()   { echo "  ok    $1"; }
bad()  { echo "  FAIL  $1"; fail=1; }

echo "== cyclaw-gotchas verify =="
bash -n "$here/driver.sh" && ok "driver.sh parses" || bad "driver.sh has a syntax error"

name=$(sed -n 's/^name: *//p' "$here/SKILL.md" | head -1)
[ "$name" = "$(basename "$here")" ] && ok "frontmatter name matches directory ($name)" || bad "frontmatter name '$name' != directory '$(basename "$here")'"
grep -q '^description:' "$here/SKILL.md" && ok "frontmatter has a description" || bad "frontmatter lacks description"

# Every repo path the skill cites must exist -- a moved checker or skill would
# otherwise make the instructions silently wrong.
missing=0
for p in $(grep -o '`[.A-Za-z0-9_/-]*\.\(py\|sh\|md\|yaml\|yml\|toml\|txt\|env\)`' "$here/SKILL.md" | tr -d '`' | sort -u); do
  case "$p" in /*|~*) continue ;; esac
  case "$p" in */*) ;; *) continue ;; esac   # bare basenames (ci.yml, SKILL.md) are mentions, not paths
  [ -e "$repo_root/$p" ] || { echo "        missing: $p"; missing=1; }
done
[ "$missing" -eq 0 ] && ok "every repo path cited in SKILL.md exists" || bad "SKILL.md cites a path that no longer exists"

[ -f "$repo_root/.claude/commands/$name.md" ] && ok "slash-command wrapper present" || bad ".claude/commands/$name.md missing"
grep -q "\`/$name\`" "$repo_root/CLAUDE.md" && ok "listed in CLAUDE.md §9" || bad "not listed in CLAUDE.md §9 (doc-sync D1 will flag it)"

if bash "$here/driver.sh" inventory > /tmp/cyclaw-gotchas-inventory.txt 2>&1; then
  ok "driver.sh inventory runs ($(grep -c . /tmp/cyclaw-gotchas-inventory.txt) lines)"
else
  bad "driver.sh inventory failed:"; tail -5 /tmp/cyclaw-gotchas-inventory.txt
fi

if [ "$fail" -eq 0 ]; then echo "== cyclaw-gotchas verify: OK =="; else echo "== cyclaw-gotchas verify: FAIL =="; fi
exit $fail
