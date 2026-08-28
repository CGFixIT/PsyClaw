# `macos/scripts/` — operator screenshot for the Apple Silicon onboarder

This folder is **docs-only**. The live scripts still live one directory up in `macos/` (`setup-cyclaw.sh`, `setup-from-clone.sh`, `setup-cyclaw-keys.sh`, `install-cyclaw.sh`, `invoke-cyclaw.sh`, …). Nothing here is imported by `gate.py` / `graph.py` / `mcp_hybrid_server.py` (I6).

![Apple Silicon onboarding: one script, fewer rituals](readme.png)

`readme.png` is the operator-facing walkthrough of `bash macos/setup-cyclaw.sh --autofill-api-key`:

- one command, Keychain + `~/.CyClaw/.env` mode `0600`, loopback-only autofill
- no `localStorage` / cookie / HTML default for `CYCLAW_API_KEY`
- wrapper does not edit `soul.md`, does not enable agentic writes, does not enter the request path

Authoritative flag list and persistence contract: [`../README.md`](../README.md) and `bash macos/setup-cyclaw.sh --help`.
