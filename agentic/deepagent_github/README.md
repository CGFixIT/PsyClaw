# `agentic/deepagent_github/` — real-repo workspace tools + retired DeepAgents graph

This package holds two distinct subsystems:

- **Live:** `repo_workspace.py`'s `RepoWorkspaceTools` (clone/read/write_file/
  commit/push, jailed via `agentic/fsconnect/pathsafe.ScopedRoots`) plus
  `chat_client.py`/`model_adapter.py`, used by `agentic/real_repo_loop.py` —
  the one live real-repo coding pipeline.
- **Retired:** `builder.py`'s DeepAgents subgraph integration (owner decision
  2026-07-31). No further development is planned; `agentic/real_repo_loop.py`
  has superseded it as the live real-repo coding path. Code, tests, and the
  `deepagents-harness` CI lane remain in the repository unmodified — this is
  a documentation-only decision, not a deletion. See `builder.py`'s own
  module docstring and `docs/agentic/AGENTIC_README.md` §9 for the fuller
  account.