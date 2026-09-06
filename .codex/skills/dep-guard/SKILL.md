---
name: dep-guard
description: Run CyClaw's stdlib-only dependency contract checks before changing dependencies, install manifests, Docker, Conda, platform installers, or release paths. Use the maintained repository checkers; do not create a second pin parser.
---

# Dependency Guard

`pyproject.toml`, `requirements.txt`, `constraints.txt`, `environment.yml`,
Docker, and platform installers are selected install profiles, not identical
files. The maintained `.claude/skills/dep-guard/` and
`.claude/skills/verify-deps/` scripts are the source of truth for static
checks; this Codex skill is their safe operating guide.

## Workflow

1. Fetch current `origin/main`, read the target diff and all affected install
   workflows. Map shared manifests before branching.
2. Run the offline checks before installing anything:

   ```text
   python .claude/skills/dep-guard/check_deps.py --strict
   python .claude/skills/verify-deps/extract_pins.py --strict
   python .claude/skills/verify-deps/check_env_drift.py --strict
   ```

3. Classify each difference as base, optional, platform-specific, transitive,
   or an accepted documented exception. Keep CPU Torch install order and the
   plain-macOS Torch exception intact. The root `environment.yml` is the Conda
   profile; `.github/workflows/environment.yml` is a linted workflow no-op,
   not another dependency manifest.
4. Use dry-run installs only for the selected surface. Validate Docker/Conda
   separately when those files change; do not treat a host-pip success as a
   container proof.
5. Re-run the appropriate checker after edits and review the diff for secrets,
   unpinned tools, and accidental manifest drift.

## Verification and publication

Use `.claude/skills/dep-guard/verify.sh` and
`.claude/skills/verify-deps/verify.sh` when Bash is available; on Windows run
the Python checkers directly. Record skipped platform builds and advisory scans.
Keep dependency changes in a focused draft PR and never push `main`.
