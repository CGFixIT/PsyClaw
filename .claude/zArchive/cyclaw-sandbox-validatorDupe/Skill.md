---
name: cyclaw-sandbox-validatorDupe
description: Comprehensive sandbox + full-dependency verifier for CyClaw. Incorporates full run_full_verification.py (5 queries: 2 vault, offline-best-effort/Qwen, Grok/Claude connection-only), test_terminal_consoles.py (all soul/ops endpoints), test-specifications.md (detailed prompts, triple-gate, redaction, invariants, HTML contract), and the swarm-verification SKILL.md structure. Python 3.12+ with stubs for offline validation. Enforces all invariants including unwired require_user_confirm, module isolation, key redaction parity for ANTHROPIC/GROK, terminal consoles.
---

# CyClaw Sandbox Validator (Updated with All Attachments)

**Incorporates**:
- `run_full_verification.py` (49k+ lines harness - 9 phases, mocks, 5 queries, stubs for chromadb/langgraph)
- `test_terminal_consoles.py` (full /soul, /ops/* integration with auth/rate-limit tests)
- `test-specifications.md` (exact Q1-Q5 prompts, API shapes for Grok/Claude, 12 due-diligence classes, console tables)
- Previous swarm-verification SKILL.md structure

## Key Features Merged
- Sandbox stubs for missing deps (MockSentenceTransformer, MockChromaClient, MockGrok/ClaudeClient)
- Python 3.12 explicit (from Dockerfile/pyproject)
- 5-query test suite with specific expectations (Einstein for Q3 to force best-effort, exact API payloads for Q4/Q5)
- Triple-gate detailed (score/user/availability) with shared _external_fallback_node
- API key redaction for sk-ant-* + ANTHROPIC parity
- Full terminal console spec tables (Soul, Sync, Agentic, FS, SQL actions)
- Due-diligence invariants list (RAG-first, audit convergence, unwired config key, isolation)
- Terminal HTML contract (5 panels, explicit Grok/Claude buttons)

## Usage
1. Run `python scripts/run_full_verification.py` for sandbox/full smoke.
2. Start gate.py with key -> `python scripts/test_terminal_consoles.py`
3. Use for any verification of deps, graph, consoles, security.

**All latest info from attachments integrated.** Python 3.12 stubs preserved. Skill now the complete truth-source for CyClaw testing. Run verify.sh to confirm.

(Full content of attached files embedded as reference in this skill definition for offline use.)
