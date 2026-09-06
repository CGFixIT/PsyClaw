# Task and handoff contracts

A useful engineering handoff includes:

- Authorized outcome and task mode, with explicit exclusions where they matter.
- Actual repository/worktree, branch/PR, inspected base/head, and existing edits
  the recipient must preserve. Keep machine-specific paths in private task
  messages, never reusable public skill content.
- Observed failure/desired behavior, evidence, known contracts, and open questions.
- Smallest proposed change and acceptance checks, including failure-path evidence
  needed to falsify the hypothesis.
- Completion state: edited, tested, committed, pushed, merged, or deployed;
  include commands, results, required skips, and remaining decisions.

Do not paste private prompts, corpus data, raw audit records, credentials, or
personal details into public PRs/reports. Use synthetic fixtures and summaries.

When writing an agent prompt, define authority and acceptance criteria explicitly.
Do not imply the recipient may publish, contact others, spend money, or bypass
an approval because the prompt contains a plan. Delegate only when authorized;
separate write ownership when multiple workers are already in scope.

For multi-PR work, map shared files, consolidate related overlap, or stack actual
dependencies with the correct PR base. Trial-merge and inspect all intended
changes survive. Parent-before-child is required; numeric PR order alone is not.
Existing authorization persists, but new actions outside it still need approval.
