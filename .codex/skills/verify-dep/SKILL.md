---
name: verify-dep
description: Verify CyClaw dependency profiles across pyproject, legacy requirements, constraints, Docker, Conda, platform installers, and existing supply-chain controls. Use before changing dependencies, install manifests, Docker/Compose/deploy files, dependency CI, or a release install path.
---

# Verify CyClaw Dependencies

Use the maintained `.claude/skills/dep-guard/` and
`.claude/skills/verify-deps/` checkers; do not copy their parsers into this
skill or add a second dependency source of truth.

## Install-surface contract

These are selected profiles, not three files that must contain identical text:

| Surface | Contract |
| --- | --- |
| `pyproject.toml` + `constraints.txt` | PEP 621 base dependencies and selected optional extras; constraints caps their versions. |
| `requirements.txt` + `constraints.txt` | Legacy, CI, and base container install: core runtime, test tools, CPU Torch, and load-bearing `websockets`; it intentionally excludes opt-in extras. |
| `Dockerfile` | Consumes the legacy constrained surface above; it is not an independent dependency manifest. |
| `environment.yml` | Conda base/test/dev profile with documented Conda-only FastAPI and Starlette exceptions. |
| Platform installers | Linux/Windows install `torch==...+cpu` from the PyTorch CPU index; macOS installs plain Torch then filters Linux-only Torch/index lines from copied manifests. |

`constraints.txt` is a version ceiling, not an install list. Do not add a fake
`torch-cpu` extra or require all profiles to be byte-for-byte equal.

## Workflow

1. Read `AGENTS.md`, `.codex/Codex_instructions.md`, the target diff, and the
   relevant install/deploy workflows. Fetch `origin/main`, list open PRs, and
   map shared manifest, Docker, CI, and documentation files before editing.
   Consolidate related shared-file changes where practical; otherwise use the
   documented stacked-PR procedure.
2. Run the offline static gate before installing or updating anything:

   ```bash
   python .claude/skills/dep-guard/check_deps.py --strict
   python .claude/skills/verify-deps/extract_pins.py --strict
   python .claude/skills/verify-deps/check_env_drift.py --strict
   ```

   On POSIX hosts without `python`, replace it with `python3`. Treat any
   nonzero result as a dependency-contract failure, not a cue to add a random
   package or loosen a pin.
3. Inventory the selected profile from `pyproject.toml`: base dependencies,
   each optional-dependency group, direct source imports, and operator-supplied
   tools such as Ollama, GitHub CLI, rclone, Postgres, Docker, Falco, and
   AppArmor. Classify every difference as base, opt-in, platform-specific,
   transitive constraint, or documented exception.
4. Validate only the install surface being changed. When network/tooling is
   available, use dry-runs first:

   ```bash
   uv pip install --dry-run -e . -c constraints.txt --extra-index-url https://download.pytorch.org/whl/cpu
   uv pip install --dry-run -r requirements.txt -c constraints.txt
   ```

   For Docker or Compose changes, also run:

   ```bash
   python -m pytest tests/test_isolation_deploy.py tests/test_falco_detection.py -q -p no:cacheprovider
   docker compose config --quiet
   docker build -t cyclaw:verify-dep .
   ```

   Record a skipped Docker/Conda/platform build honestly if the host lacks that
   runtime. Do not substitute a host-pip success for a container build.
5. For macOS, validate the documented Apple-Silicon installer path separately.
   Plain macOS Torch is intentional; do not force the Linux/Windows `+cpu` pin
   into that filtered install.
6. For a pin or dependency change, use the existing `pip-audit`, OSV, Trivy,
   Dependabot, and optional-extras CI paths. Verify releases and advisories from
   authoritative sources, preserve accepted-risk documentation, and never
   auto-bump a package merely because a newer release exists.
7. Report the profile matrix, intentional exceptions, commands run, advisories
   reviewed, and any unverified platform. This skill changes no core request
   path and must preserve I1-I6, especially I6 optional-module isolation.

## PR completion gates

At the end of the implementation phase, immediately before push and draft PR,
fetch `origin/main` and rebase the feature branch onto it. Re-run the affected
checks after the rebase. After the draft PR's CI is green, fetch again: if
`main` moved, rebase onto its latest commit, re-run checks and CI, and only then
recommend merge. Force-with-lease requires explicit human approval.
