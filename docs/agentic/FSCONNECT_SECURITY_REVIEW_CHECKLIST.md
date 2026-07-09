# fsconnect Write-Enablement Security Review

Sign-off required **before** `fsconnect.writes_enabled: true` on any production
deployment. This checklist is written against the Phase 2 implementation actually
shipped in `agentic/fsconnect/` (writer, pathsafe, trash, quota, config, cli); every
item names the code or test that backs it. Flipping the write flag without a completed,
filed copy of this checklist is an unauthorized change.

```
Reviewer: ______   Date: ______   Deployment: ______   Config sha256: ______
```

## A. Invariants (each must cite the passing test)

- [ ] **I6 isolation** — `GROK_API_KEY=dummy pytest tests/test_agentic_isolation.py -q` green; `agentic/` is never imported by `gate.py`/`graph.py`/`mcp_hybrid_server.py`. `/ops/fsconnect` exposes **read-only** actions only (writes are local-CLI-only; `utils/ops_runner.py` `_FSCONNECT_ACTIONS` unchanged).
- [ ] **Four-gate pattern intact** — `tests/test_fsconnect_writer.py` gate matrix green (`writes_enabled` → dry-run; empty reason → `failed_gate="reason"`; destructive without confirm → `failed_gate="confirm"`).
- [ ] **Purge fifth gate** — `test_purge_refused_without_allow_hard_delete` green: `allow_hard_delete: false` refuses `delete --purge` with `failed_gate="allow_hard_delete"`.
- [ ] **Two-phase audit** — `test_intent_precedes_applied` green: `fsconnect_write_intent` is logged before `fsconnect_write_applied` for the same op.
- [ ] **pathsafe adversarial matrix** — `tests/test_fsconnect_pathsafe.py` green (symlink/`..`/absolute-path/overlap containment).

## B. Configuration posture

- [ ] `writes_enabled` currently **FALSE** (the flip is the last step of the playbook, not this review).
- [ ] `writable_roots`: minimal set; none inside the repo, inside `data/corpus/` (the write→index loop would self-amplify), or inside a read root (`allowed_roots`).
- [ ] `strict_roots: true` — a root that cannot be prepared fails closed (`FsPathError`) rather than silently falling back to `~/CyClaw-FS`. With `false`, a fallback emits an audited `fsconnect_root_fallback` event (config drift signal).
- [ ] `allow_hard_delete: false` unless hard delete is justified in writing. This is a **global** flag: it gates `delete --purge` and `trash-empty` for every root, with no per-root granularity.
- [ ] `quota_bytes` (and optionally `max_files`) set on every root via the mapping form of `writable_roots`; filesystem has ≥ 2× headroom over the sum of quotas.
- [ ] `write_rate_limit.enabled: true` with a persisted `db_path` (separate sqlite file from the gateway limiter; default `data/fsconnect_rate.db`).
- [ ] `require_confirm_destructive: true`.
- [ ] `allow_unc_roots: false` unless a UNC/network root was deliberately reviewed.

## C. OS posture

- [ ] A dedicated non-root OS user owns each writable root; mode `0700`/`0750`; no setuid bits under any root.
- [ ] Roots are on a local filesystem (not NFS/SMB), OR network-share risk is formally accepted (R-1 residual).
- [ ] `logs/` is not inside any writable root; `audit.jsonl` is `chattr +a` (append-only) where available, or shipped off-host (R-9 is open — see playbook §10).
- [ ] Platform is POSIX. **Windows write-enablement is REFUSED until Phase 4**: the Windows write paths in `pathsafe.py` are `# pragma: no cover` and unverified; do not set `writes_enabled: true` on Windows.

## D. Threat-model spot-checks (execute, do not assume)

- [ ] A symlink planted inside a root is not followed on write (pathsafe `O_NOFOLLOW` descent; `follow_symlinks` is a hard-false config error).
- [ ] Target `../escape` is refused at `split_components` (`FsPathError`).
- [ ] `fs_write` into `.cyclaw-trash/<forged>` is refused (`failed_gate="reserved_name"`); writing `.cyclaw-quota.json` or a `*.cyclaw-tmp` leaf is likewise refused.
- [ ] An append loop past a root's `quota_bytes` is refused with `failed_gate="quota"` (`test_append_loop_eventually_denied`).
- [ ] More than `max_ops` writes in a window are refused with `failed_gate="rate_limit"` **across separate CLI invocations** (sqlite persistence; `test_limit_persists_across_invocations`).
- [ ] `kill -9` mid-write leaves no partial file visible (`O_EXCL` tmp + atomic `os.replace` + parent-dir fsync); the orphaned `*.cyclaw-tmp` is swept by `trash-empty`.
- [ ] Audit review: after the above, every `fsconnect_write_intent` has a matching `fsconnect_write_applied` or `fsconnect_write_refused` (grep the audit JSONL by `intent_id`). **Note:** there is no `audit-verify` subcommand in Phase 2 — this is a manual grep until it ships.

## E. Compliance controls mapped

- [ ] The control mapping in the playbook (§8) has been verified against the buyer's framework (NIST 800-171 / CMMC 2.0 / HIPAA / SOC 2).

## F. Rollback rehearsal

- [ ] `writes_enabled` was flipped back to `false` and a write was observed to return a dry-run plan (proving rollback #1 works with no restart) **before** go-live.

```
Sign-off: ____________________   (no sign-off, no flag flip)
```
