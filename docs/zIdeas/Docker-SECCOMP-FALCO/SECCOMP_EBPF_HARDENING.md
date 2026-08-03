# CyClaw eBPF + Seccomp Hardening #679 and 725

Where this fits in the overall posture: see [`docs/THREAT_MODEL.md`](./THREAT_MODEL.md)
(§3 what the sandbox covers, §6 the hardening maturity ladder).

## What it is

- **seccomp**: a Linux kernel filter that restricts which syscalls a process may
  make (e.g. block `mount`, `ptrace`, `reboot`, raw sockets). Default Docker
  blocks ~50 dangerous ones; a custom profile tightens further.
- **eBPF**: programmable kernel observability — here used to (a) **detect**
  anomalous behaviour at runtime (Falco, detection-only) and (b) **trace** the
  app's *actual* syscalls so a tight seccomp allow-list can be generated from real
  data instead of guessed.

## Current state

| Layer | File | Status |
|---|---|---|
| seccomp — rclone/agentic subprocess set (applied to the app service) | `deploy/seccomp/sync-rclone.json` | ✅ Wired in `docker-compose.yml` |
| seccomp — legacy broad rclone profile | `deploy/seccomp/rclone-seccomp.json` | Reference only |
| seccomp — minimal gate floor (16 syscalls) | `deploy/seccomp/gate-seccomp.json` | ⚠️ **NOT wired** — see below |
| eBPF detection (Falco) | `deploy/falco/` | ✅ Scaffold shipped, **disabled by default** |
| eBPF-profiled tight gate seccomp | — | 🔜 Roadmap (depends on traces) |
| Landlock / AppArmor | — | 🔜 Roadmap |

### Why `gate-seccomp.json` is intentionally not wired

It lists only 16 syscalls and **cannot boot** `uvicorn`+`torch`+`chromadb`
(missing `clone`, `brk`, `mprotect`, `getrandom`, `rt_sigaction`, …). Applying it
would crash the server, not harden it. A correct gate-specific block-list must be
**generated from real syscall traces** (next section), then swapped in. Until
then the gate runs under the broader working profile.

## Generate a tight profile from real traces

```bash
# Option A — Falco/eBPF (see deploy/falco/README.md): run the monitor, exercise
# every endpoint, collect the syscall set the gate actually uses.
docker compose --profile monitoring up

# Option B — Podman syscall tracer:
podman run --annotation io.containers.trace-syscall=of:gate-trace.json ...
# then exercise /health, /query, /soul/* and build the allow-list from gate-trace.json
```

Apply the generated profile via `security_opt: [seccomp:./deploy/seccomp/<profile>.json]`
in `docker-compose.yml`, then **verify the container still boots and `/health`
returns 200** before committing.

## Runtime detection (Falco)

`deploy/falco/` adds an opt-in, **detection-only** eBPF tripwire over the
out-of-band agentic/sync write paths and the gate's egress. It logs, never blocks,
and ships disabled (privileged sidecar). Enable with
`docker compose --profile monitoring up`. Full details:
[`deploy/falco/README.md`](../deploy/falco/README.md).

## Next

- Build and wire the eBPF-profiled gate seccomp block-list (ladder stage 3).
- Add Landlock + AppArmor filesystem confinement (ladder stage 4).
