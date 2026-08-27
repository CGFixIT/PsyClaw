# CyClaw Docker / GHCR distribution

**Status:** runtime image publish path (GHCR) · operator-controlled mounts · loopback-only host publish.

This document is the operator guide for running CyClaw from the published container
image. It does **not** replace [`setup-guide.md`](../setup-guide.md) (native install)
or [`docs/THREAT_MODEL.md`](./THREAT_MODEL.md) (threat scope).

## What is published

| Artifact | Registry | Name |
|---|---|---|
| Runtime image | GHCR | `ghcr.io/cgfixit/cyclaw` |

Tags (from `.github/workflows/publish-ghcr.yml`):

| Trigger | Tags |
|---|---|
| Git tag `v1.9.1` | `1.9.1`, `1.9`, `latest` |
| Manual `workflow_dispatch` | `sha-<short>`, `edge` |

Architecture today: **`linux/amd64` only**. Apple Silicon Macs should use the
native install path ([`macos/install-cyclaw.sh`](../macos/install-cyclaw.sh) /
[`docs/HARNESS_MACOS.md`](./HARNESS_MACOS.md)) or Docker Desktop until a
dedicated `linux/arm64` image is verified against the torch pin.

## What is *not* in the image (by design)

`.dockerignore` + the multi-stage `Dockerfile` keep private / regenerable state out
of layers:

- `config.yaml` (operator-owned; bind-mount read-only)
- `data/` corpus vectors, runtime caches
- `index/` ChromaDB + BM25 state
- `logs/`, `checkpoints/`
- `.env`, keys, `*.pem`
- tests, docs, `.git`, `.github`

The image is the **gate + graph + retrieval runtime** only. Soul personality file
that ships in git may be present; treat production `soul.md` as operator-owned and
bind-mount if you customize it.

## Security posture (must preserve)

These match root `docker-compose.yml` and [`docs/THREAT_MODEL.md`](./THREAT_MODEL.md):

| Control | How it is enforced |
|---|---|
| Loopback-only exposure | Host publish `127.0.0.1:8787:8787` — **never** `0.0.0.0` |
| Non-root | `user: "1000:1000"` (Dockerfile `cyclaw` uid) |
| No privilege escalation | `no-new-privileges:true`, `cap_drop: ALL` |
| Read-only rootfs | `read_only: true` + tmpfs `/tmp` + explicit rw mounts |
| Seccomp | `seccomp:builtin` (see [`deploy/seccomp/README.md`](../deploy/seccomp/README.md)) |
| Resource caps | mem / pids / cpus in compose |
| Optional detection | Falco eBPF sidecar **off by default** (`--profile monitoring`) |

Invariants I1–I6 are unchanged: the container runs the same `gate.py` / `graph.py`
code. Loopback is a **host** boundary; TrustedHostMiddleware remains a second layer.

### Falco (optional — detection only)

Do **not** enable by default. The monitoring profile is **privileged** (host kernel
eBPF). It logs only; it does not block. Full rules and validate-before-trust
checklist: [`deploy/falco/README.md`](../deploy/falco/README.md).

```bash
docker compose --profile monitoring up -d
docker logs -f cyclaw-falco
```

### MicroVM / gVisor / Firecracker

**Not shipped.** CyClaw's threat model is single-operator loopback + container
hardening, not a multi-tenant microVM. Do not add Firecracker/gVisor/Kata wiring
without a separate threat-model amendment and Stage-3 seccomp-style evidence.
See the scope statement in [`docs/THREAT_MODEL.md`](./THREAT_MODEL.md).

### AppArmor

Optional host profile scaffold: [`deploy/apparmor/`](../deploy/apparmor/). Not
required for GHCR pull/run.

## Prerequisites

1. Docker Engine **23.0+** (for `seccomp:builtin`) + Compose v2
2. Local dirs / files next to compose:
   - `config.yaml` (from the repo or your hardened copy)
   - `data/`, `index/`, `logs/`, `checkpoints/` (created empty if needed)
3. Ollama (or LM Studio) **outside** the image — typically on the host

## Quick path: pull + compose

```bash
# Authenticate only if the package is private
# echo "$GITHUB_TOKEN" | docker login ghcr.io -u USERNAME --password-stdin

export CYCLAW_IMAGE_TAG=1.9.0   # or 1.9.1 after that tag is published
docker pull "ghcr.io/cgfixit/cyclaw:${CYCLAW_IMAGE_TAG}"

# From a checkout that has docker-compose.yml + config.yaml + data mounts
docker compose pull
docker compose up -d
curl -sS http://127.0.0.1:8787/health
```

`docker-compose.yml` sets both `image:` and `build:`. Operators who prefer a local
build can still `docker compose build` without changing the file.

## Minimal `docker run` (parity with compose hardening)

```bash
docker run -d \
  --name cyclaw \
  -p 127.0.0.1:8787:8787 \
  -v "$(pwd)/data:/app/data:rw" \
  -v "$(pwd)/index:/app/index:rw" \
  -v "$(pwd)/checkpoints:/app/checkpoints:rw" \
  -v "$(pwd)/logs:/app/logs:rw" \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --security-opt seccomp=builtin \
  --user 1000:1000 \
  --memory 4g \
  --pids-limit 256 \
  --cpus 2.0 \
  -e PYTHONPATH=/app \
  -e HF_HOME=/app/data/.hf_cache \
  -e HUGGINGFACE_HUB_CACHE=/app/data/.hf_cache \
  -e SENTENCE_TRANSFORMERS_HOME=/app/data/.st_cache \
  "ghcr.io/cgfixit/cyclaw:${CYCLAW_IMAGE_TAG:-1.9.0}"
```

> The old `-e CYCLAW_TELEMETRY_KILL=1` line is gone on purpose: no code ever
> read that name. The image itself now carries the real canonical
> telemetry-kill and update-check values as `ENV` (checker-pinned against
> `utils/telemetry_kill.py`), so every process in the container -- the
> uvicorn CMD, the HEALTHCHECK probe, any `docker exec` -- starts inside
> the canonical environment with nothing to pass at `docker run` time.

## Ollama / local model

Do **not** bake Ollama or model weights into the CyClaw image.

- **Linux host network:** point `config.yaml` `models.local_llm.base_url` at a
  reachable host address. From a bridge network container, `host.docker.internal`
  may require `extra_hosts: ["host.docker.internal:host-gateway"]` on Linux.
- Prefer keeping the model on the host and treating network egress as an explicit
  operator choice (Falco outbound rules assume loopback/local Ollama by default).

Optional cloud extras (`agentic-deepagents`, etc.) are **not** in the image
(`requirements.txt` only). Install inside a derived image only if you accept that
surface — never for the default offline path.

## Publishing (maintainers)

Workflow: [`.github/workflows/publish-ghcr.yml`](../.github/workflows/publish-ghcr.yml)

```bash
# After main is green and you intend a release:
git tag v1.9.1
git push origin v1.9.1
# Actions → Publish GHCR runs; image appears at ghcr.io/cgfixit/cyclaw:1.9.1
```

Manual smoke (no semver): Actions → **Publish GHCR** → Run workflow → tags
`edge` + `sha-…`.

First-time package settings (org/user admin):

1. Confirm package `cyclaw` under GHCR for `CGFixIT` / `cgfixit`
2. Link package to the `CGFixIT/CyClaw` repository
3. Set visibility (private recommended for high-trust MSP delivery; public only if
   intentional)
4. Optional: require Actions environment protection before enabling a `ghcr`
   environment gate in the workflow

## Related files

| Path | Role |
|---|---|
| [`Dockerfile`](../Dockerfile) | Multi-stage, digest-pinned base + uv, non-root, healthcheck |
| [`docker-compose.yml`](../docker-compose.yml) | Host loopback publish + hardening + optional Falco profile |
| [`.dockerignore`](../.dockerignore) | Keeps state/secrets out of build context |
| [`deploy/seccomp/README.md`](../deploy/seccomp/README.md) | Builtin seccomp status; Stage 3 custom profile not yet generated |
| [`deploy/falco/README.md`](../deploy/falco/README.md) | Opt-in eBPF detection (privileged sidecar) |
| [`deploy/apparmor/`](../deploy/apparmor/) | Optional AppArmor scaffold |
| [`docs/SECCOMP_EBPF_HARDENING.md`](./SECCOMP_EBPF_HARDENING.md) | Trace → tighter block profile procedure |
| [`docs/THREAT_MODEL.md`](./THREAT_MODEL.md) | Authoritative scope (incl. no microVM by design) |

## What not to do

- Do not publish `latest` from untagged `main` commits
- Do not relax host publish to `0.0.0.0`
- Do not enable Falco monitoring by default
- Do not bake secrets, corpus vectors, or production config into image layers
- Do not treat GHCR as a substitute for the six invariants or human-gated agentic writes
- Do not claim multi-tenant / microVM isolation for this image
