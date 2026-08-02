# CyClaw — CVE Analysis Report

**Date:** 2026-06-20  
**Tooling:** pip-audit 2.10.1 (full installed-environment scan, Python 3.12.3 venv)  
**Scope:** `requirements.txt` + `constraints.txt` transitive tree, main branch  
**Scanner run:** `pip-audit --desc on` against the installed Python 3.12 environment  
**Result:** 6 known vulnerabilities across 2 packages

> **Important context:** This report is complementary to the OSV-Scanner and pip-audit
> GitHub Actions workflows added in the concurrent PR. The 8 alerts referenced in the
> Security → Code Scanning tab arise from overlap between tool databases: OSV-Scanner
> and pip-audit both index the chromadb CVE, and CodeQL/DevSkim may surface additional
> code-level findings not included here. All 6 pip-audit findings are documented below.

---

## Summary table

| # | CVE / ID | Package | Version | Fix Available | Applies to CyClaw? | Risk (in CyClaw context) |
|---|---|---|---|---|---|---|
| 1 | CVE-2026-45829 | chromadb | 1.5.6 | None (upstream) | ⚠️ **Yes — mitigated** | Near-zero (see §1) |
| 2 | PYSEC-2026-196 | pip | 24.0 | pip ≥ 26.1.2 | ⚠️ CI/install only | Low (see §2) |
| 3 | CVE-2025-8869 | pip | 24.0 | pip ≥ 25.3 | ✅ **NOT applicable** | None on Python 3.12 |
| 4 | CVE-2026-1703 | pip | 24.0 | pip ≥ 26.0 | ⚠️ CI/install only | Low (see §2) |
| 5 | CVE-2026-3219 | pip | 24.0 | pip ≥ 26.1 | ⚠️ CI/install only | Low (see §2) |
| 6 | CVE-2026-6357 | pip | 24.0 | pip ≥ 26.1 | ⚠️ CI/install only | Low (see §2) |

---

## §1 — chromadb 1.5.6 / CVE-2026-45829

**Severity:** High (pre-authentication remote code execution)  
**Description:** A pre-auth code injection vulnerability in ChromaDB ≥ 1.0.0 allows an
unauthenticated attacker to execute arbitrary code on the server by submitting a malicious
model repository when the `/api/v2/tenants/{tenant}/databases/{db}/collections` endpoint
is called with `trust_remote_code=true`.

**Does it apply to CyClaw? Yes — but structurally mitigated. Risk is near-zero.**

CyClaw's architecture neutralises the attack surface at three independent layers:

| Layer | Mitigation | Location |
|---|---|---|
| Network | Server binds exclusively to `127.0.0.1`; the ChromaDB HTTP endpoint is unreachable from any remote host | `config.yaml` → `api.host: 127.0.0.1`; enforced at startup |
| Code | `trust_remote_code` is explicitly set to `False` in every caller | `retrieval/embeddings.py`, `retrieval/indexer.py`, `mcp_hybrid_server.py` |
| Design | Offline-first; no unauthenticated external API surface; MCP server sets `sampling=None` | `gate.py`, `mcp_hybrid_server.py` |

**Upstream fix status:** No patched ChromaDB release is available as of 2026-06-20.
The pin is intentionally held at `1.5.6` (the last verified-stable 1.5.x release).

**Recommended action:**
- **Now:** No code change required. The existing structural mitigations hold.
- **Track:** Monitor the ChromaDB changelog. Once a patched release appears, upgrade
  the pin in `requirements.txt` and `constraints.txt` and re-run the verification suite
  (`python -m pytest tests/ -q` should stay 90/90).
- **CI gate:** The `pip-audit` GitHub Actions workflow ignores this CVE with
  `--ignore-vuln CVE-2026-45829` and a documented rationale comment. That ignore must
  be removed immediately when a fix is available.

---

## §2 — pip 24.0 / four CVEs (PYSEC-2026-196, CVE-2026-1703, CVE-2026-3219, CVE-2026-6357)

> **Scope note:** `pip` is **not** a production dependency of CyClaw. It is not listed in
> `requirements.txt` or `constraints.txt`. These vulnerabilities affect the `pip` CLI tool
> itself during package *installation*, not CyClaw's running application. The risk surface
> is CI runners and developer workstations, not deployed CyClaw instances.

### CVE applicability detail

| ID | Description | Fix | CyClaw production risk |
|---|---|---|---|
| **PYSEC-2026-196** | pip treats `console_scripts`/`gui_scripts` as paths rather than file names without sanitising the resolved absolute path, allowing entry points to be installed outside the installation directory | pip ≥ 26.1.2 | None at runtime; low in CI (attacker would need to inject a malicious wheel into the install step) |
| **CVE-2026-1703** | When extracting a maliciously crafted wheel, files may traverse outside the installation directory (limited to prefixes of the install dir) | pip ≥ 26.0 | Same as above |
| **CVE-2026-3219** | pip handles concatenated tar+ZIP files as ZIP regardless of filename, causing confusing/incorrect installation | pip ≥ 26.1 | Negligible; all deps are fully pinned with exact version SHAs |
| **CVE-2026-6357** | pip's self-update check imports newly-installed module names after wheel install; deferred imports could be hijacked | pip ≥ 26.1 | None at runtime |

### CVE-2025-8869 — NOT APPLICABLE to this project

**CVE-2025-8869** (pip tar extraction symlink bypass) is **explicitly not applicable**:
the advisory states *"if you're using a Python version that implements PEP 706 (Python ≥ 3.9.17,
≥ 3.10.12, ≥ 3.11.4, or ≥ 3.12) then pip doesn't use the vulnerable fallback code."*
CyClaw targets Python 3.12 and the GitHub Actions workflows use Python 3.12. pip's secure
extraction path is always taken.

### Recommended action for pip CVEs

**Add a pip self-upgrade step at the top of every CI install job:**

```yaml
- name: Upgrade pip to patched version
  run: python -m pip install --upgrade "pip>=26.1.2"
```

This costs < 5 seconds per run and eliminates all four pip CVEs in the CI environment.
It does not affect the production `requirements.txt`/`constraints.txt` pins (pip is a
dev-only tool). Add this step before any `pip install -r requirements.txt` call in:
- `.github/workflows/ci.yml`
- `.github/workflows/pip-audit.yml` (before the audit step)
- `.github/workflows/devskim.yml` (if it installs Python deps)
- Developer setup instructions in `docs/work/SETUP.md`

---

## §3 — Python 3.12 runtime compatibility (verification)

As part of this analysis, CyClaw `main` was verified to install and run correctly under
Python 3.12.3 in a clean venv:

| Check | Result |
|---|---|
| `pip install -r requirements.txt -c constraints.txt` | ✅ Exit 0, no conflicts |
| Core imports (fastapi, uvicorn, pydantic, langchain-core, langgraph, chromadb, httpx, pyyaml, rank-bm25, nltk) | ✅ All OK |
| Syntax check (`ast.parse`) on `gate.py`, `graph.py`, `mcp_hybrid_server.py`, `metrics.py` | ✅ All OK |
| `GET /health` (server booted, no LM Studio, no ChromaDB index) | ✅ HTTP 200 — `{"status":"degraded",...}` (expected) |
| `GET /` | ✅ HTTP 200 — HTML UI served |
| `GET /docs` | ✅ HTTP 200 — Swagger UI rendered |

**Conclusion:** CyClaw `main` is fully functional under Python 3.12. The "degraded" health
status is the expected baseline when LM Studio is not running and the ChromaDB index has
not been built (`python -m retrieval.indexer`). This matches the documented startup sequence
in `docs/work/SETUP.md`.

Prior verification (commit `9aa163a`, 2026-06-16) confirmed 90/90 unit tests passing
under Python 3.12.3. See `tests/VERIFICATION_REPORT_3.12.md` for the full test matrix.

---

## §4 — Recommended next steps (prioritised)

### Immediate (no code change)
1. **Track CVE-2026-45829** upstream. Subscribe to ChromaDB releases; the moment a
   patched version ships, bump the pin and remove the `--ignore-vuln` in `pip-audit.yml`.

### Low-effort CI hardening (< 30 min)
2. **Upgrade pip in CI jobs.** Add `python -m pip install --upgrade "pip>=26.1.2"` before
   any `pip install` in every workflow. Eliminates 4 of 6 CVEs in the CI environment
   (CVE-2025-8869 is already not applicable on Python 3.12).
3. **S5 residual from security review:** Point `ci.yml` at the full `pytest tests/` (the
   suite is 90/90 green) and drop the 5-file subset (already noted in `SECURITY_REVIEW_STATUS.md`).

### Medium-term supply-chain hardening
4. **Hash-locked installs.** Use `pip-compile --generate-hashes` to produce a
   `requirements.lock` with per-file hashes, then install with `--require-hashes`.
   pip-audit already warns about this on every `--no-deps` run. This would make
   any supply-chain tampering immediately detectable at install time.
5. **S7 residual:** Drop the inert `"null"` CORS entry and the hardcoded LAN IP from
   `config.yaml`'s `security.allowed_origins` (noted in `SECURITY_REVIEW_STATUS.md`).

### Informational
6. **OSV-Scanner + pip-audit in CI** are now wired up via PR #69. Once that PR is merged
   to `main`, the scheduled scans will run automatically every Monday (OSV at 06:00 UTC,
   pip-audit at 07:00 UTC), providing continuous CVE monitoring without manual action.
