# `deploy/` — container hardening profiles (all opt-in)

Linux-host containment and detection scaffolding for the Docker deployment.
Nothing in this tree is enabled by default, and nothing here is imported by
any Python module — these are host/daemon configs an operator applies
deliberately. Each subdirectory carries its own README with status, scope,
and apply/rollback steps; this file is the index.

| Subdirectory | What it is | Status (see its README) |
|---|---|---|
| `apparmor/` | AppArmor profile (`cyclaw-gate`) + compose overlay for a single-container deployment | Opt-in candidate; not runtime-validated under Docker Desktop's Linux VM |
| `seccomp/` | seccomp policy notes; `docker-compose.yml` pins `seccomp:builtin` explicitly | Docker builtin enforced; custom profile not yet generated |
| `falco/` | Falco eBPF detection rules — a tripwire that logs anomalous syscalls, **not** a containment boundary | Detection-only, disabled by default |
| `planning/` | Working notes (`todo.txt`) for this tree | Scratch |

## Related

- Container build/run: [`docs/DOCKER.md`](../docs/DOCKER.md)
- Hardening rationale and staging: [`docs/SECCOMP_EBPF_HARDENING.md`](../docs/SECCOMP_EBPF_HARDENING.md)
- What CyClaw does and does not defend against: [`docs/THREAT_MODEL.md`](../docs/THREAT_MODEL.md)
