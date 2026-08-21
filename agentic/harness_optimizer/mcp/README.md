# `agentic/harness_optimizer/mcp/` — proposer workspace tools

**Not an MCP server.** The directory is named `mcp` because these wrappers are
the future MCP *tool boundary*. This package does not import or start
`mcp_hybrid_server.py`, (<-Which is the mcp server module) and it does not speak the MCP protocol.

Public export: `ProposerWorkspaceTools` (also re-exported from
`agentic.harness_optimizer`).

Constraints enforced here (same as a future MCP surface would have to):

- no host shell
- no GitHub writes
- no unrestricted filesystem
- no holdout reads
- writes only under `current/` or via `finish_proposal`

| Tool | Purpose |
|---|---|
| `list_workspace` | List visible entries (skips `holdout_hidden`) |
| `read_file` | Read one visible file (≤ 256 kB, 256,000 bytes) |
| `read_surface_manifest` | Local surface manifest |
| `read_train_failures` | Visible train artifacts |
| `read_visible_history` | Prior-attempt artifacts |
| `rag_search_readonly` | Injected RAG or empty results |
| `write_current_file` | Atomic write under `current/` only |
| `finish_proposal` | Atomic write of `proposal.md` |

Parent package: [`../README.md`](../README.md). The same table lives in
[`../../README.md`](../../README.md) §G.
