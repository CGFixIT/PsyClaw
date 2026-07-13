# Pre-PR Checklist

- Summarize the change and why it is needed.
- Confirm no existing Claude/Copilot/Codex instructions were overwritten unintentionally.
- Confirm Codex guidance links to canonical repo files instead of carrying
  duplicate instruction snapshots.
- Run the lightest meaningful verification from `AGENTS.md`.
- For broad changes, mirror the relevant command sequence in `.github/workflows/ci.yml`.
- Check optional-layer isolation: `sync/`, `agentic/`, and `guardrails/` should not become required by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`.
- Confirm loopback-only binding remains intact unless the maintainer approved otherwise.
- Note whether `gh` or GitHub connector permissions were needed and whether they worked.
- Include commands run, failures, skipped checks, and residual risk in the PR body.
