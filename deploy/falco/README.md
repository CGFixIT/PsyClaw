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
   the shipped default (`127.0.0.1:11434`). Keep the destination address and
   port joined with `and`; a port-only alternative permits that port on every
   external host.

## Compose command: defaults before CyClaw rules

`docker-compose.yml` starts Falco with **two** `-r` paths, in this order:

1. `/etc/falco/falco_rules.yaml` — the image-shipped default ruleset (macros
   like `spawned_process`, `open_write`, `outbound`).
2. `/etc/falco/rules.d/cyclaw_rules.yaml` — this package's overlay.

Any explicit `-r` makes Falco ignore `falco.yaml`'s `rules_files` list. If you
only pass the CyClaw file, default macros are missing and the sidecar can
fail/restart-loop. Do not reorder or drop the default `-r` without updating
the rules and `tests/test_falco_detection.py`.

## Validate before trust (Linux + Docker; not a Windows CI gate)

Static tests in-repo only check YAML shape. Before you treat Falco alerts as
signal on a real host:

```bash
# 1) Start the optional monitoring profile (privileged eBPF sidecar)
docker compose --profile monitoring up -d

# 2) Confirm the modern eBPF probe loaded and rules parsed
docker logs cyclaw-falco 2>&1 | head -n 80
# Expect no continuous restart; look for rules loaded / modern_bpf ready.

# 3) Optional: Falco native rule validation against the two ordered files
#    (paths inside the running container or a one-shot of the same image)
docker exec cyclaw-falco falco --dry-run \
  -r /etc/falco/falco_rules.yaml \
  -r /etc/falco/rules.d/cyclaw_rules.yaml

# 4) Sanity: unexpected egress should still fire for non-loopback:11434
#    (do not rely on port 11434 alone as an allow-list).

docker logs -f cyclaw-falco
```

Kernel need: ≥ ~5.8 for `--modern-bpf` (CO-RE). Image pin:
`falcosecurity/falco:0.39.2`. See also
[`docs/SECCOMP_EBPF_HARDENING.md`](../../docs/SECCOMP_EBPF_HARDENING.md) for
turning traces into a tighter *block* profile later — Falco here only logs.

## Requirements & caveats

- Linux host with a kernel new enough for Falco's modern eBPF probe
  (≥ 5.8; the image pin is `falcosecurity/falco:0.39.2`).
- Will **not** run in environments without host kernel access (most CI, many
  managed/rootless container hosts). That is expected — it is an operator-run
  monitor, not a CI gate.
- Detection only. To *block* syscalls, tighten the seccomp profile instead — and
  do that only after using these traces to build a verified allow-list (see
  [`docs/SECCOMP_EBPF_HARDENING.md`](../../docs/SECCOMP_EBPF_HARDENING.md)).
- The monitoring profile is **privileged**. Leave it off unless you need the
  eBPF tripwire and accept host-kernel access for the sidecar.
