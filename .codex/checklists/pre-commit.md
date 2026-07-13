# Pre-Commit Checklist

- Read `AGENTS.md` and relevant subsystem docs.
- Confirm the diff is scoped to the task.
- Avoid committing logs, caches, indexes, `.env`, `rclone.conf`, coverage artifacts, or local scratch files.
- For Python changes, run or justify skipping:
  - `ruff check --select E,F,I,B,C4,UP,S .`
  - targeted `pytest` for touched modules
- For retrieval changes, prepare runtime dirs and run `python -m tests.ci_rag_smoke` when feasible.
- For dependency changes, compare `pyproject.toml`, `requirements.txt`, `constraints.txt`, and `Dockerfile`.
- For security-sensitive changes, check `docs/THREAT_MODEL.md` and `.github/SECURITY.md`.
- For `.codex/skills` changes, validate touched `SKILL.md` frontmatter,
  `agents/openai.yaml`, bundled Python, and shell syntax as applicable.
- Record any skipped verification in the final response.
