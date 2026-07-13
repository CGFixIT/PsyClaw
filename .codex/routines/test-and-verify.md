# Test And Verify Routine

## When To Use

Use this when choosing checks before/after a change, validating CI parity, or reporting verification honestly.

## Inputs To Ask For

- What changed or what needs confidence.
- Whether local dependencies are installed.
- Whether external services such as LM Studio, rclone, Postgres, or gh are available.

## Workflow

1. Read `AGENTS.md` verification sections.
2. For Python changes, start with targeted pytest for touched modules.
3. For retrieval changes, run `python -m tests.ci_rag_smoke` when the environment is prepared.
4. For lint-only confidence, run `ruff check --select E,F,I,B,C4,UP,S .`.
5. For broad release-risk changes, mirror `.github/workflows/ci.yml`.
6. For agentic changes, set `GROK_API_KEY` to a non-secret dummy value in the
   active shell, run the targeted agentic tests, and run
   `python -m agentic.cli test` if `gh` availability matters.
7. For skill changes, validate each touched skill folder, parse
   `agents/openai.yaml`, compile bundled Python, run shell syntax checks, and
   confirm the default prompt names the exact `$skill-name`.
8. For routine, prompt, or checklist-only changes, prefer markdown review and
   stale-string searches.
9. Record skipped checks with the reason.

## Verification Checklist

- Runtime prep done where needed: `data/personality`, `index`, `logs`, dummy `GROK_API_KEY`.
- Commands came from repo docs/config/CI.
- Failures were diagnosed before retrying.
- External dependencies were not assumed.
- Sandbox or approval limits were reported instead of hidden.

## Expected Final Response

- Commands run, exactly.
- Pass/fail/skip status.
- Failure snippets or reasons.
- Recommended next verification step.
