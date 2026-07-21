## Title
**Use this format:**  
`[prefix] - Short descriptive sentence of the change`

**Recommended prefixes (pick the most relevant):**  
`[invariant]` • `[governance]` • `[fsconnect]` • `[agentic]` • `[rag]` • `[harness]` • `[security]` • `[docs]` • `[infra]` • `[fix]` • `[feat]`

Example: `[governance] - add two-phase audit + quota enforcement to fsconnect write path`

---

## Proposed changes
Describe the big picture of your changes here. Explain **why** maintainers should accept this PR.  
If it fixes a bug or resolves a feature request, link the issue.

**Invariant / Governance Impact** (required for any change touching core paths):
- Which of the 6 security invariants or I6 module isolation does this change affect (or confirm none)?
- Provide evidence it is preserved (e.g., graph topology unchanged, audit convergence maintained, soul evolution still human-gated, RAG-first entry point intact).
- If you are intentionally relaxing or evolving an invariant, explain the justification and compensating controls.

---

## Types of changes
What types of changes does your code introduce to CyClaw?  
_Put an `x` in the boxes that apply_

- [ ] Bugfix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation Update (if none of the other choices apply)
- [ ] Invariant / Governance refinement (use this for changes that strengthen or evolve the 6 invariants, I6 isolation, or harness phases)

**Optional free-text scope note** (recommended):  
Core graph/gate/soul path | Out-of-band agentic/fsconnect/sync layer | RAG retrieval/sanitization | Docs + audits | Infrastructure / CI only

---

## Benefits / why
- Why make this change? What is the concrete upside for CyClaw users, operators, or long-term maintainability?
- How does this improve (or at least not degrade) production readiness, invariant strength, offline/air-gapped reliability, governance observability, or security posture?
- For agentic or fsconnect changes: how does this increase governed capability without weakening the read-only core contract?

---

## Risks to monitor
- What are the potential regressions, negative side-effects, or things that need extra attention after merge?
- Could this introduce a new shortcut path around audit convergence, weaken RAG-first enforcement, create network assumptions, affect subprocess isolation, or change soul evolution behavior?
- For write-enablement or quota changes: what failure modes exist if the two-phase audit or trash retention logic has a bug?
- How will you (or future maintainers) detect drift from the intended behavior?

---

## Checklist
_Put an `x` in the boxes that apply. You can fill these out after creating the PR. If you're unsure about any item, ask before opening the PR._

- [ ] I have read the latest `docs/CyClaw Architecture Guide` (and any relevant Phase docs) and `SECURITY.md`
- [ ] This change preserves all 6 security invariants and I6 module isolation (explicit evidence or invariant matrix included for core changes)
- [ ] Full sandbox validation has been run (`cyclaw-sandbox-validator` or equivalent pytest + smoke tests on core RAG/agentic paths) and passes with no regressions
- [ ] No new external network dependencies or mandatory online LLM assumptions were introduced without explicit justification + offline fallback path
- [ ] For any agentic/fsconnect/harness change: two-phase audit, quota enforcement, governed delete/trash, and write guards have been verified
- [ ] Relevant architecture docs, threat model notes, or harness phase documentation have been updated if core behavior or topology changed
- [ ] Commit messages follow the title prefix convention above
- [ ] For large or complex changes: before/after invariant matrix + sandbox evidence is included in "Further comments" or linked

---

## Further comments
If this is a relatively large, complex, or core-path change, kick off the discussion here.

**For changes touching `graph.py`, `gate.py`, soul paths, RAG retrieval/sanitization, or agentic subsystems, include:**
- Explicit before/after invariant matrix
- Sandbox validation diff or key evidence
- Any compensating controls or observability added

**Examples of what good "Further comments" look like for core changes:**
- "No change to graph topology or entry points. RAG-first and audit convergence remain enforced by edges only."
- "Added governed write path behind fifth gate + two-phase audit. Core request path untouched. Full sandbox run attached."
- "Relaxed one non-critical logging path for observability; compensating SHA-256 audit still converges. See attached invariant matrix."

---

**Notes for contributors (especially solo maintainer PRs):**
- Core invariant or governance changes require the strongest evidence.
- Out-of-band layers (agentic/, sync/, .claude/) can use a lighter path but still need Benefits + Risks + relevant checklist items.
- Docs-only or audit PRs can skip some technical items but must still complete Benefits / Risks.
- The goal is production-grade discipline without unnecessary ceremony. Brutal honesty on impact is expected and appreciated.
