# `agentic/deepagent_github/` — real-repo workspace tools + retired DeepAgents graph

This package holds two distinct subsystems:

- **Live:** `repo_workspace.py`'s `RepoWorkspaceTools` (clone/read/write_file/
  commit/push, jailed via `agentic/fsconnect/pathsafe.ScopedRoots`) plus
  `chat_client.py`/`model_adapter.py` and `handoff.py`, used by
  `agentic/real_repo_loop.py` — the one live real-repo coding pipeline.
  `handoff.py`'s `sanitize_handoff` is on the outbound path of every cloud
  call: `chat_client.py` runs it on each prompt before it leaves the machine,
  bounded by `max_handoff_chars`, and emits an `agentic_deepagent_cloud_handoff`
  audit event.
- **Retired:** `builder.py`'s DeepAgents subgraph integration (owner decision
  2026-07-31). No further development is planned; `agentic/real_repo_loop.py`
  has superseded it as the live real-repo coding path. Code, tests, and the
  `deepagents-harness` CI lane remain in the repository unmodified — this is
  a documentation-only decision, not a deletion. See `builder.py`'s own
  module docstring and `docs/agentic/AGENTIC_README.md` §9 for the fuller
  account.

## Which module is on which side

Every module in the package, so the live/retired boundary is not left to
inference:

| Module | Side | Note |
|---|---|---|
| `repo_workspace.py` | Live | `RepoWorkspaceTools`; the jailed clone/read/write/commit/push surface |
| `chat_client.py` | Live | Cloud provider client used by `real_repo_loop.py` |
| `model_adapter.py` | Live | Provider/model shaping for the above |
| `handoff.py` | Live | `sanitize_handoff` — bounds and redacts every outbound cloud prompt |
| `builder.py` | Retired | The DeepAgents subgraph itself |
| `core.py` | Retired | Serves the `deepagent-plan` probe only |
| `runners.py` | Retired | Serves the `deepagent-plan` probe only |
| `memory.py` | Retired | Imported by `builder.py` only |
| `permissions.py` | Retired | Imported by `builder.py` only |
| `skills.py` | Retired | Imported by `builder.py` only |
| `subagents.py` | Retired | Imported by `builder.py` only |
| `tools.py` | Retired | Imported by `builder.py` only |

Retired here means "not developed further and not on the live path" — not
"unreachable". `agentic/cli.py`'s `deepagent-plan` subcommand still imports
`builder`/`core`/`runners`, and the `deepagents-harness` CI lane still exercises
them.