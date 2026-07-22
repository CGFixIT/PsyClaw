# CyClaw Falco / eBPF detection scaffold

**Status: detection-only · disabled by default · opt-in.**

This is an eBPF tripwire, **not** a containment boundary. [Falco](https://falco.org)
watches kernel syscalls and **logs** when the CyClaw container does something
outside its known-good behaviour. It never blocks a call. It is defense-in-depth
*observability* layered on top of the real controls (loopback-only binding,
read-only rootfs, dropped capabilities, seccomp, and CyClaw's topology/injection
gates). See [`docs/THREAT_MODEL.md`](../../docs/THREAT_MODEL.md) for where this
sits in the overall posture and what it does **not** cover.

## What it watches

[`falco_rules.yaml`](./falco_rules.yaml) ships four CyClaw-specific rules:

| Rule | Fires when | Priority |
|---|---|---|
| Unexpected process spawned | A binary other than `python`/`rclone`/`gh`/`uvicorn` execs in the app container | WARNING |
| Shell spawned in container | Any shell (`bash`/`sh`/…) starts — CyClaw never uses one | CRITICAL |
| Write outside allowed roots | A write lands outside `data/logs/checkpoints/.emb_cache/tmp` | ERROR |
| Unexpected outbound connection | Egress to anything other than loopback / local Ollama | WARNING |

These map directly to the out-of-band agentic & sync write paths and the gate's
egress — the surfaces a 2026 review would (fairly) want eyes on.

## Why it ships disabled

Falco needs a **privileged** sidecar with host kernel access (`/proc`,
`/sys/kernel/debug`, the modern eBPF probe). That privilege is itself attack
surface, so it is gated behind a Compose profile and off by default. A plain
`docker compose up` never starts it.

## Enable it

```bash
# Bring up CyClaw + the Falco monitor together:
docker compose --profile monitoring up

# Tail what Falco sees:
docker logs -f cyclaw-falco
```

Before relying on the alerts, tune two things in `falco_rules.yaml` to your
deployment:

1. `cyclaw_container` — the app container name (default `cyclaw-prod`).
2. `cyclaw_expected_outbound` — your Ollama host/port if the model is not on
   the shipped default (`127.0.0.1:11434`).

## Requirements & caveats

- Linux host with a kernel new enough for Falco's modern eBPF probe
  (≥ 5.8; the image pin is `falcosecurity/falco:0.39.2`).
- Will **not** run in environments without host kernel access (most CI, many
  managed/rootless container hosts). That is expected — it is an operator-run
  monitor, not a CI gate.
- Detection only. To *block* syscalls, tighten the seccomp profile instead — and
  do that only after using these traces to build a verified allow-list (see
  [`docs/SECCOMP_EBPF_HARDENING.md`](../../docs/SECCOMP_EBPF_HARDENING.md)).
