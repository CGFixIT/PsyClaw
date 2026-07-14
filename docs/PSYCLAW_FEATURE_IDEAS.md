# Not now def later once ive done more of the stuff im supposed to update this and see if it holds up to scrutiny theres so many of these docs randomly (note: create a local claude side save or hook or pre commit scan/check to have it story ik local .claude or memory with an automation weekly to store on a computrr 
# i dunno but telhere is something that can be done since theyre just not things you put here but i need to somehow read and organize them - worst case just write a script or sonething in ckaude that glues os and an event in claude that i could make trigger something
# (that might be something cuz that surprisingly haopens a lot ive hesrd a lot of people say 
# anyways im done typing here hopefully i know what i was thinking there later 
(hint: gh and not kimi ai being clever the git command to commit ha

- that generates from a scheduled full git clone sandbox scan thats scripted to automate 

# PsyClaw - Regulated SMB Feature Hypotheses

CyClaw began as **PsyClaw**, an offline-first RAG assistant aimed at small and
mid-sized businesses under real compliance pressure: law firms,
psychology/therapy practices, medical/dental offices, and accounting shops.
Those segments are plausible, not validated. Treat them as discovery targets
until buyer conversations prove urgency, budget, procurement path, and trust
requirements.

## Current product facts

- **Repo-backed:** CyClaw's security posture is RAG-first retrieval,
  topology-as-policy, triple-gated external calls, audit convergence, and human
  governance for soul changes. A feature that erodes those invariants is not
  worth shipping.
- **Repo-backed:** `GET /audit/summary` is an API-key-gated, read-only endpoint
  over existing audit aggregates. It reports counts and distributions; it does
  not expose raw queries or per-record rows.
- **Business status:** as of the 2026-07-03 business review, net-new product
  features should stay frozen unless a customer, paid pilot, or interview use
  case creates a concrete reason to build them.

## Evidence labels

- **Repo-backed fact:** implemented behavior visible in code.
- **Market signal:** credible external pressure, but not proof CyClaw can sell.
- **Hypothesis:** plausible buyer need that needs discovery or paid validation.
- **Not claimed:** legal advice, SOC 2 readiness, HIPAA compliance, BAA coverage,
  a formal compliance program, or audited certification.

## 1. Audit / compliance summary endpoint

**Status:** repo-backed current behavior.

**What:** `GET /audit/summary` returns aggregate metrics over the audit log:
total events, event breakdown, RAG-query count, score distribution,
retrieval-mode mix, model-usage breakdown, and external-LLM escalation count.

**Commercially safe claim:** this endpoint can help an operator answer basic
operational questions such as "how often did the system escalate outside the
local model?" or "what retrieval modes were used?"

**Boundary:** this is operational evidence, not a SOC 2 artifact, legal opinion,
HIPAA control, BAA substitute, or auditor-ready compliance binder.

**Possible extension:** a portable evidence export may be useful later, but only
after discovery confirms that buyers or auditors actually ask for that package.
Hash chains, PDF/CSV exports, retention controls, and external anchors belong in
a separate design review before implementation.

## 2. Retention and right-to-erasure tooling

**Status:** hypothesis.

**What:** a controlled purge for audit and interaction records older than a
defined retention period, exposed through a local operator workflow.

**Market signal:** regulated buyers often care about retention and
data-minimization, but the specific retention policy is vertical-specific and
must be validated with the buyer's counsel, insurer, or compliance owner.

**Security constraints:** any deletion path must be explicit, auditable,
fail-closed, and non-autonomous. It must not corrupt append-only logs or create a
new plaintext persistence path.

**Build trigger:** a paid pilot or discovery call where retention is named as a
purchase blocker.

## 3. Matter / client tagging and conflict-wall checks

**Status:** hypothesis, law-firm specific.

**What:** optional matter/client tags that flow into audit reporting as a tag
dimension, plus a local conflict-wall check for walled-off work.

**Market signal:** legal workflows are matter-centric, and regulated buyers may
need usage reports by client or engagement.

**Risk:** tags are sensitive metadata. A safe design should avoid storing client
names in plaintext and should keep checks local.

**Build trigger:** discovery proves that matter-level reporting is more valuable
than generic audit summaries and that buyers will pay for it.

## Guiding principle

Do not turn market pressure into product claims. CyClaw's strongest proven value
today is a credible local-governance engineering artifact and portfolio signal.
Commercial work should start with buyer discovery, attorney/IP review, and
constrained paid pilots, not speculative feature expansion.
