---
description: >-
  Session-tested traps for working on CyClaw from a Claude Code sandbox (proxy-denied torch and Hugging Face hosts, the 3.12 venv, the silent pytest summary, PR/check-in/review-bot process, the harness single-stream 409s) plus a driver that builds the venv, launches/probes/stops gate.py, runs tests with a visible summary, and runs the stdlib guards. Load before installing deps, running tests, launching the server, opening or driving a PR, answering a bot review, or when something hangs, will not install, or says 409 busy.
---

Invoke the `cyclaw-gotchas` skill for the given task. $ARGUMENTS

See `.claude/skills/cyclaw-gotchas/SKILL.md` for full detail, and run
`bash .claude/skills/cyclaw-gotchas/driver.sh inventory` first.

## Notes

- Driver subcommands: `inventory`, `venv`, `serve`, `probe`, `stop`, `test [paths]`, `checks`.
- CLAUDE.md §4 is the codebase trap list; this skill is the session trap list. Where they overlap, CLAUDE.md wins.
- Add a gotcha only with evidence (date, PR, or file:line); expire one the same way.
