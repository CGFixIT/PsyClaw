---
name: injection-redteam
description: Exercise CyClaw's shipped sanitizer against the repository probe corpus and adversarial boundary cases. Use for sanitizer reviews, prompt-injection regression checks, or before changing banned patterns; do not silently close known findings.
---

# Injection Red Team

Use the maintained probe corpus and runner under
`.claude/skills/injection-redteam/`. A flagged `open_finding` is a known gap;
an unflagged bypass is a regression. Never delete a probe to make the run pass.

Keep probes local with synthetic content. HTTP `/query` and retrieval-only MCP
both sanitize input; source scanning is not proof that every entry point invokes
the scanner. Test the relevant route and do not send private corpus or prompts
to an external model to judge a bypass.

## Workflow

1. Read `config.yaml`, `utils/sanitizer.py`, the probe corpus, and the threat
   model. Treat this as a trust-boundary review, not a regex-only exercise.
2. Run the baseline:

   ```text
   python .claude/skills/injection-redteam/redteam.py --json
   ```

3. Add at least one local adversarial probe for the requested boundary:
   Unicode/confusables, mixed case, whitespace/newline splits, empty input,
   very long input, or a harmless near-miss. Keep it outside the repository's
   corpus unless the user asks to add a durable regression anchor.
4. If the sanitizer changes, run the baseline again, targeted tests, and the
   invariant guard. Review false positives as well as bypasses; do not weaken
   RAG-first or audit behavior to improve a probe score.
5. Run `.claude/skills/injection-redteam/verify.sh` when Bash and the project
   environment are available. Record environment limitations honestly.

## Safety

Do not send probes to external providers, expose corpus contents in PR text,
or change configuration solely to silence a finding. Keep remediation separate
from an audit if the root cause needs a policy decision.
