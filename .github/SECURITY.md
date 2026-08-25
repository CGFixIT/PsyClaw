# Security Policy — CyClaw

CyClaw is a production-grade local AI agent built on three invariants: RAG-first retrieval, LangGraph topology as security policy, and human-gated soul governance. Security is a top priority. This document describes how to report vulnerabilities, how we triage and respond, and what is in-scope for vulnerability reports.

> **Two files, one policy.** This is the copy GitHub displays (`.github/` wins the community-health precedence over the repo root). The root [`SECURITY.md`](../SECURITY.md) is the companion: it carries the **security model summary** (the enforced invariants) and the **Accepted Dependency Risks** register — the chromadb and nltk CVE acceptances that `requirements.txt`, `.trivyignore`, `.osv-scanner.toml`, and the `pip-audit` workflow all encode. Reporting process lives here; risk acceptances live there. Update the one that owns the section rather than copying it across.

---

## Contact — reporting a vulnerability

Preferred secure channel:
- GitHub Security Advisories: https://github.com/CGFixIT/CyClaw/security/advisories (recommended) — this provides a private, encrypted channel to the maintainers.
- If that is unavailable to you, contact the maintainer through [cgfixit.com](https://cgfixit.com) and ask for secure-upload instructions before sending any detail.

Do NOT open a regular GitHub issue for a vulnerability. On a public repository every issue is public the instant it is filed — there is no per-issue "private" setting — so an issue is a disclosure, not a report. Do not post vulnerabilities publicly anywhere (issues, public threads) before a coordinated disclosure. See the Disclosure and Timeline section below.

---

## In-scope

Anything in this repository or code we distribute as part of CyClaw including, but not limited to:
- The CyClaw agent core code and runtime (Python code in this repo).
- RAG (retrieval) integrations and retrieval pipeline code in this repo.
- LangGraph topology definitions and enforcement code.
- Integrations and adapter code provided in the repo (connectors, local plugins).
- Configuration parsing and policy enforcement components included here.

Out-of-scope:
- Third-party closed-source models and hosted model providers (OpenAI, Anthropic, etc.) — report issues to those vendors unless the problem is caused by our code or integration logic.
- Plugins or third-party services not hosted in this repository (unless we maintain the integration code here).

---

## AI-specific threats we care about

When reporting, consider these AI/agent specific attack classes (examples):
- Prompt injection and instruction-stealing that causes the agent to ignore LangGraph security constraints.
- RAG/data leakage: retrieval or generation that exposes sensitive documents or unredacted secrets.
- Model-poisoning or malicious document attacks that influence retrieval/ranking in unsafe ways.
- Sandbox escape or arbitrary code execution through plugin/adapters the agent uses.
- Improper access control in LangGraph topology enforcement allowing escalation of privileges.
- Data exfiltration via logs, traces, or network integrations.

---

## How to report

Include as much of the following as possible. If you must include exploits or PoCs with sensitive data, redact secrets and coordinate privately.

Required/Recommended report contents:
- A short summary of the issue and impact.
- Step-by-step reproduction steps (commands, inputs, dataset samples).
- Which version/commit of CyClaw you tested (git SHA / tag).
- Environment details: OS, Python version, dependencies (pip freeze), container details if used.
- Expected behavior vs. observed behavior.
- PoC code or a safe minimal demo (redact any real secrets).
- Any suggestions for mitigation or fix if available.
- Contact information (GitHub username and email) and whether you want credit for reporting.

Do not send private keys, real passwords, or other secrets in your initial report. If you need to share sensitive data to demonstrate the issue, request secure upload instructions in the initial report.

Suggested subject line format (advisory title or email): SECURITY: <component> - <short summary>

---

## Triage and timeline

We aim to handle reports quickly and responsibly.

- Acknowledgement: within 3 business days
- Initial triage and severity classification: within 7 business days
- Patch or mitigation release: target within 30 days for High/Critical, 60–90 days for Medium depending on complexity; Low will be scheduled as part of normal maintenance
- CVE coordination: when appropriate, we will request a CVE and coordinate disclosure
- Public disclosure: we will coordinate with the reporter and follow the Disclosure and Timeline policy below

Timelines are target goals; actual times may vary depending on complexity and available information. We will keep the reporter informed throughout.

---

## Severity guidance

Use the following guidance when estimating impact; we will assign final severity:

- Critical: Remote unauthenticated arbitrary code execution or total data exposure of secrets used in production, or full LangGraph policy bypass that can cause real-world harm.
- High: Privilege escalation, confidential data exfiltration from local stores, arbitrary plugin execution in common deployment modes.
- Medium: Authentication bypass for non-critical flows, partial data leakage, or denial-of-service targeting the agent.
- Low: Minor information leakage, UI issues, or low-risk misconfigurations.

If you're unsure, report the issue; we'll triage.

---

## Coordinated disclosure and CVE

We prefer coordinated disclosure:
- Report vulnerabilities privately and allow us time to fix and release before public disclosure.
- Typical coordinated disclosure window is up to 90 days. For Critical issues that are being actively exploited, we may expedite disclosure.
- We will credit reporters who request credit (GitHub handle or real name) unless they request anonymity.
- We will work with the reporter to request CVE identifiers when applicable.

---

## Handling of exploit code, PoCs, and patches

- If your PoC includes exploit code, mark it as such and only share it through secure channels.
- We may publish sanitized PoC code with the fix for educational purposes, in coordination with the reporter.
- When possible, we will publish fixes as backported patches to supported releases and a public advisory.

---

## Legal safe harbor

We appreciate security research. Please follow responsible disclosure and avoid breaking applicable laws. We will not pursue legal action against individuals who follow these reporting guidelines and who test only resources they own or have explicit permission to test, provided actions follow applicable law and the reporter respects safe disclosure.

---

## Reporting checklist (quick)

1. Prefer a private GitHub Security Advisory (or the maintainer contact at cgfixit.com if unavailable). Never a public GitHub issue.
2. Provide reproduction steps, environment, git SHA, and a safe PoC.
3. Avoid including real secrets.
4. Expect an acknowledgement within 3 business days.

---

## Maintenance and contact

Maintainers will update this document as processes evolve. For urgent security contact, use the preferred method listed at the top.

Thank you for helping keep CyClaw safe.
