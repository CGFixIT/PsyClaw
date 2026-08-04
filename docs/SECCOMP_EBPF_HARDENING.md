---
title: "CyClaw Container Isolation Hardening"
date: 2026-08-03
tags: [security, seccomp, apparmor, ebpf, falco, gvisor, firecracker]
related:
  - docs/THREAT_MODEL.md
  - deploy/seccomp/README.md
  - deploy/apparmor/README.md
  - deploy/falco/README.md
---

# CyClaw container isolation hardening

This is the operator plan for Stages 3 through 5 of the isolation ladder. It
assumes one trusted operator, a trusted host root, no public or multi-tenant
service, and host publication only on `127.0.0.1:8787`. It does not weaken or
replace CyClaw's six application invariants.

## 1. Reality check

**Production Isolation Reality: 4/10 relative to gVisor/Firecracker-grade
hostile-workload containment.** The loopback publish, non-root UID,
`no-new-privileges`, dropped capabilities, read-only root filesystem, resource
limits, and Docker's explicitly selected builtin seccomp profile are a reasonable
baseline for CyClaw's trusted single-operator model. Falco adds an optional
detection scaffold, but it is disabled by default and never blocks. Residual
risk remains: the container shares the host kernel; allowed syscalls still
expose kernel attack surface; ordinary TCP/UDP sockets can exfiltrate readable
data; writable data/index/log/cache/checkpoint paths can be destroyed; hostile
pytest or `conftest.py` code can access anything visible to UID 1000; and a
Falco alert arrives after the syscall. `cap_drop: ALL` should deny privileged
raw/packet sockets, but that is a negative test to retain, not a reason to claim
kernel isolation.

The former repository seccomp profiles were not a working Stage 3. The profile
wired to the gate omitted normal Uvicorn/Python syscalls including `listen`,
`accept*`, `exit_group`, clocks, and thread setup; the supposed gate floor was
x86-64-only; and the legacy rclone file contained invalid syscall names. They
have been removed. Compose now forces `seccomp:builtin`, so a daemon configured
with another default or `unconfined` cannot silently weaken the service. Docker
describes its builtin as a moderately protective compatibility baseline and
recommends not replacing it casually.

## 2. Stage 3 - tight gate-specific seccomp

### Status and decision

**Capture-ready, not enforced.** There is no honest minimal
`deploy/seccomp/gate-seccomp.json` to commit from this Windows host. A valid
profile depends on the exact Linux architecture, kernel, OCI runtime, image
digest, Python/libc, Torch, Chroma, and exercised code paths. Trace `arm64` and
`amd64` independently; never translate an x86 list into an Apple-silicon Linux
profile.

There is also a process-boundary conflict. `gate.py` registers `/ops/*`, and
`utils/ops_runner.py` launches Python CLIs that can launch `git`, `gh`,
`rclone`, pytest, Ruff, indexers, and schedulers. Seccomp is inherited by child
processes and cannot be widened under `no-new-privileges`. Therefore:

1. A gate-only profile cannot safely promise support for `/ops`; some children
   may fail while Python-only children still share many gate syscalls.
2. A monolithic profile that supports every optional child must union their
   syscall sets and is not gate-specific.
3. A truly tight design requires a separate optional ops/executor service with
   its own policy. That split is deferred until untrusted-workload mode is real.

### Record the target

Run on each target Linux environment, including the Linux VM used by an
Apple-silicon container engine:

```bash
uname -m
uname -r
grep CONFIG_SECCOMP= "/boot/config-$(uname -r)"
docker version --format '{{.Server.Version}}'
docker compose build cyclaw
IMAGE_ID="$(docker compose images -q cyclaw)"
docker image inspect "$IMAGE_ID" --format '{{.Id}} {{.Architecture}} {{.Os}}'
```

Require Docker Engine 23.0 or newer for the explicit `seccomp:builtin`
selector. Fail closed rather than removing the selector on an older engine.
Record the image's actual name from `docker compose images` if Compose chooses a
different project prefix. Repeat capture after a base-image, architecture,
Python/libc, Torch, Chroma, Docker Engine, or material runtime-path change.

### Primary capture: OCI seccomp eBPF hook

The hook attaches before the container starts, avoiding startup gaps. It
requires root, BCC/kernel headers, and an OCI hook-capable Linux runtime; it
cannot be used with `podman run --rm`.

Fedora-family setup:

```bash
sudo dnf install -y podman oci-seccomp-bpf-hook jq strace
sudo podman build -t cyclaw-gate-seccomp-trace .

TRACE_DIR="$(mktemp -d)"
TRACE_1="$TRACE_DIR/gate-$(uname -m)-1.json"
TRACE_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
printf 'TRACE_DIR=%s\n' "$TRACE_DIR"
```

Use throwaway copies of private state for soul mutation, recovery, and failure
tests. The following preserves the normal read-only root and writable carve-out
shape while tracing:

```bash
TRACE_STATE="$(mktemp -d)"
cp -a data "$TRACE_STATE/data"
cp config.yaml "$TRACE_STATE/config.yaml"
mkdir -p "$TRACE_STATE/checkpoints" "$TRACE_STATE/logs" "$TRACE_STATE/emb-cache"
mkdir -p "$TRACE_STATE/index"
[ ! -d index ] || cp -a index/. "$TRACE_STATE/index/"
[ ! -d checkpoints ] || cp -a checkpoints/. "$TRACE_STATE/checkpoints/"
[ ! -d logs ] || cp -a logs/. "$TRACE_STATE/logs/"
[ ! -d .emb_cache ] || cp -a .emb_cache/. "$TRACE_STATE/emb-cache/"

# Trace against a live local model without host networking. The version tag is
# trace-only; record the resolved digest and pin that digest for repeated runs.
sudo podman network create cyclaw-trace-net
sudo podman volume create cyclaw-trace-ollama
TRACE_OLLAMA_IMAGE="docker.io/ollama/ollama:0.32.3"
sudo podman pull "$TRACE_OLLAMA_IMAGE"
sudo podman image inspect "$TRACE_OLLAMA_IMAGE" \
  --format '{{.Digest}} {{.Id}} {{.Architecture}}'
sudo podman run -d --name cyclaw-trace-ollama \
  --network cyclaw-trace-net \
  -v cyclaw-trace-ollama:/root/.ollama \
  "$TRACE_OLLAMA_IMAGE"
until sudo podman exec cyclaw-trace-ollama ollama list >/dev/null 2>&1; do sleep 1; done
TRACE_MODEL="$(python -c 'import sys,yaml; from pathlib import Path; print(yaml.safe_load(Path(sys.argv[1]).read_text())["models"]["local_llm"]["model"])' "$TRACE_STATE/config.yaml")"
sudo podman exec cyclaw-trace-ollama ollama pull "$TRACE_MODEL"
python -c 'import sys,yaml; from pathlib import Path; p=Path(sys.argv[1]); c=yaml.safe_load(p.read_text()); c["models"]["local_llm"]["base_url"]="http://cyclaw-trace-ollama:11434/v1"; p.write_text(yaml.safe_dump(c, sort_keys=False))' "$TRACE_STATE/config.yaml"
sudo chown -R 1000:1000 "$TRACE_STATE"

sudo podman run -d --name cyclaw-gate-trace \
  --annotation "io.containers.trace-syscall=of:${TRACE_1}" \
  --network cyclaw-trace-net \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev \
  --user 1000:1000 \
  --memory 4g \
  --pids-limit 256 \
  --cpus 2 \
  -p 127.0.0.1:8787:8787 \
  -e "CYCLAW_API_KEY=$TRACE_API_KEY" \
  -v "$TRACE_STATE/data:/app/data:rw,Z" \
  -v "$TRACE_STATE/index:/app/index:rw,Z" \
  -v "$TRACE_STATE/checkpoints:/app/checkpoints:rw,Z" \
  -v "$TRACE_STATE/logs:/app/logs:rw,Z" \
  -v "$TRACE_STATE/emb-cache:/app/.emb_cache:rw,Z" \
  -v "$TRACE_STATE/config.yaml:/app/config.yaml:ro,Z" \
  cyclaw-gate-seccomp-trace
```

From the same shell so `$TRACE_API_KEY` remains available, exercise concrete
HTTP paths. Soul state is the throwaway copy above, never the repository's live
state:

```bash
curl -fsS http://127.0.0.1:8787/health
curl -fsS http://127.0.0.1:8787/
curl -fsS http://127.0.0.1:8787/terminal.html >/dev/null
curl --max-time 700 -fsS \
  -H 'Content-Type: application/json' \
  -d '{"query":"What are CyClaw security invariants?"}' \
  http://127.0.0.1:8787/query

seq 1 4 | xargs -I{} -P4 curl --max-time 700 -fsS \
  -H 'Content-Type: application/json' \
  -d '{"query":"Summarize the local security model."}' \
  http://127.0.0.1:8787/query >/dev/null

curl -fsS -H "Authorization: Bearer $TRACE_API_KEY" \
  http://127.0.0.1:8787/soul >/dev/null
curl -fsS -H "Authorization: Bearer $TRACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"new_soul":"# Soul\nCalm, factual, and local-first.","reason":"trace throwaway soul write"}' \
  http://127.0.0.1:8787/soul/propose >/dev/null
curl -fsS -H "Authorization: Bearer $TRACE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"new_soul":"# Soul\nCalm, factual, and local-first.","reason":"trace throwaway soul write"}' \
  http://127.0.0.1:8787/soul/apply >/dev/null
curl -fsS -X POST -H "Authorization: Bearer $TRACE_API_KEY" \
  http://127.0.0.1:8787/soul/reload >/dev/null
curl -fsS -X POST -H "Authorization: Bearer $TRACE_API_KEY" \
  http://127.0.0.1:8787/soul/restore >/dev/null
curl -fsS -H "Authorization: Bearer $TRACE_API_KEY" \
  http://127.0.0.1:8787/audit/summary >/dev/null

curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Content-Type: application/json' -d '{"query":""}' \
  http://127.0.0.1:8787/query
```

The sidecar is reachable only on the private container network; only CyClaw's
port is published, and only on host loopback. Do not use host networking: the
image command binds Uvicorn to `0.0.0.0`, which would expose it on every host
interface under host networking.

### Independent corroboration while the first container is running

Use Inspektor Gadget as a second collector before stopping
`cyclaw-gate-trace`. Pin the gadget image to a reviewed digest for a production
capture. It attaches after container startup; the OCI hook remains the evidence
source for startup syscalls.

```bash
sudo ig run \
  ghcr.io/inspektor-gadget/gadget/advise_seccomp:latest \
  --containername cyclaw-gate-trace
```

Use `strace` from another terminal for syscall names, children, file
descriptors, and argument review. Set `TRACE_DIR` there to the path printed by
the setup shell. Press Ctrl-C only after replaying the workload. Do not publish
the trace: filenames and arguments can expose private paths or content.

```bash
TRACE_DIR='/tmp/paste-the-printed-mktemp-directory-here'
PID="$(sudo podman inspect --format '{{.State.Pid}}' cyclaw-gate-trace)"
sudo strace -ff -yy -s 256 -p "$PID" -o "$TRACE_DIR/gate.full"

# On a separate fresh run, attach this instead and replay the same workload.
sudo strace -f -qq -c -p "$PID" -o "$TRACE_DIR/gate.counts"
```

Attach either full tracing or syscall counts per run, not both. These attach
captures are post-start corroboration; they do not replace the OCI hook.

Stop cleanly so the hook emits the profile:

```bash
sudo podman stop --time 30 cyclaw-gate-trace
sudo podman rm cyclaw-gate-trace
jq empty "$TRACE_1"
```

Accumulate a second independent run without losing the first syscall set:

```bash
TRACE_2="$TRACE_DIR/gate-$(uname -m)-2.json"
sudo podman run -d --name cyclaw-gate-trace-2 \
  --annotation "io.containers.trace-syscall=if:${TRACE_1};of:${TRACE_2}" \
  --network cyclaw-trace-net \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --read-only --tmpfs /tmp:rw,nosuid,nodev --user 1000:1000 \
  --memory 4g --pids-limit 256 --cpus 2 \
  -p 127.0.0.1:8787:8787 \
  -e "CYCLAW_API_KEY=$TRACE_API_KEY" \
  -v "$TRACE_STATE/data:/app/data:rw,Z" \
  -v "$TRACE_STATE/index:/app/index:rw,Z" \
  -v "$TRACE_STATE/checkpoints:/app/checkpoints:rw,Z" \
  -v "$TRACE_STATE/logs:/app/logs:rw,Z" \
  -v "$TRACE_STATE/emb-cache:/app/.emb_cache:rw,Z" \
  -v "$TRACE_STATE/config.yaml:/app/config.yaml:ro,Z" \
  cyclaw-gate-seccomp-trace
```

Replay the workload, then stop cleanly and validate the union output. Remove
the trace-only model only after all captures are complete:

```bash
sudo podman stop --time 30 cyclaw-gate-trace-2
sudo podman rm cyclaw-gate-trace-2
jq empty "$TRACE_2"

sudo podman rm -f cyclaw-trace-ollama
sudo podman network rm cyclaw-trace-net
sudo podman volume rm cyclaw-trace-ollama
```

### Required workload corpus

Every capture candidate must cover:

- Cold startup/import, Torch and sentence-transformer load, and graceful
  `SIGTERM` shutdown.
- `/health`, `/`, `terminal.html`, and static assets.
- Cold and warm RAG-hit `/query` requests with Chroma, BM25, embedding cache,
  and a live local model.
- Concurrent queries, rate-limit success/refusal, timeout, disconnect, and
  model-unavailable paths.
- Index/cache creation, Chroma locking/persistence/recovery, audit/log writes,
  and restart recovery.
- Soul read plus propose/apply/reload/restore against throwaway data only.
- Missing/corrupt index, invalid request, read-only/full-path, and clean failure
  paths.
- Explicitly gated Grok/Claude calls only for the deployment variant that will
  actually permit them.
- Every `/ops` action in a separate trace. Never silently fold those syscalls
  into a file named `gate-seccomp.json`.
- Separate traces for MCP, harness, sync, Telegram, indexer, and a future
  executor service.

### Build the candidate without deleting upstream protections

The generated output is evidence, not a deployable policy. Start from the
Moby/Docker default matching the deployed Engine version and remove only
unobserved unconditional permissions. Preserve every architecture mapping,
capability condition, and argument filter. In particular, preserve restrictions
on `clone`, namespaces, `socket` address families, `socketcall`, personality,
and privileged syscalls. A generated generic `socket: SCMP_ACT_ALLOW` rule can
undo maintained address-family protections.

Review and replay:

```bash
jq empty deploy/seccomp/gate-seccomp.candidate.json
docker compose config

docker run --rm \
  --security-opt no-new-privileges \
  --security-opt seccomp="$PWD/deploy/seccomp/gate-seccomp.candidate.json" \
  --cap-drop ALL --read-only --tmpfs /tmp \
  cyclaw-gate-seccomp-trace \
  python -c "import torch, chromadb, uvicorn; print('imports-ok')"
```

Then replay the entire workload and hostile negative tests through multiple
boot/load/shutdown cycles. Only after native `arm64` and `amd64` validation may
the reviewed file be renamed to `deploy/seccomp/gate-seccomp.json` and wired:

```yaml
services:
  cyclaw:
    security_opt:
      - no-new-privileges:true
      - seccomp:./deploy/seccomp/gate-seccomp.json
```

Verify enforcement and keep a one-command rollback to Docker's builtin:

```bash
docker compose up --build
docker inspect cyclaw-prod --format '{{json .HostConfig.SecurityOpt}}'
docker exec cyclaw-prod awk '/^Seccomp/ {print}' /proc/1/status

# rollback: replace the custom seccomp line with `seccomp:builtin`, then recreate
docker compose config
docker compose up -d --force-recreate cyclaw
```

## 3. Stage 4 - AppArmor; Landlock deferred

### AppArmor artifact

`deploy/apparmor/cyclaw-gate` is a concrete, opt-in, single-container profile.
`deploy/apparmor/docker-compose.apparmor.yml` selects it without changing the
default Compose deployment. It allows image reads and required Python/native
library mappings, but persistent filesystem writes and file locks only beneath
the paths below (with required `/dev/null`, `/dev/zero`, and `/dev/full` device
I/O):

```text
/app/data/
/app/index/
/app/logs/
/app/checkpoints/
/app/.emb_cache/
/tmp/
```

It permits IPv4/IPv6 TCP and UDP, Unix stream sockets, and unprivileged netlink
raw sockets needed by glibc address discovery. It denies IPv4/IPv6 raw, packet,
and AF_ALG sockets plus mount/umount and ptrace. It allows only the Python/Uvicorn
entry points to execute. Shells, package managers, `git`, `gh`, and `rclone` fail
closed. Python-only `/ops` children can still start and inherit the same profile,
so this is not process-role isolation.

Validate and enable using [`deploy/apparmor/README.md`](../deploy/apparmor/README.md).
Start in complain mode, replay the Stage 3 corpus, review each denial, then
enforce. `apparmor_parser -Q -W` is the syntax gate; `aa-logprof` is review
assistance, not an auto-accept step.

### Composition with the existing baseline

- `read_only: true` prevents VFS writes except explicitly writable mounts.
- AppArmor remains a second write boundary if a future mount is accidentally
  made writable.
- `no-new-privileges` prevents an `execve` from gaining privilege and keeps
  inherited restrictions from being loosened.
- `cap_drop: ALL` prevents policy manipulation and privileged raw/packet socket
  creation.
- Seccomp limits kernel entry points; AppArmor limits file/network actions even
  through allowed syscalls.

AppArmor is a Linux-host policy. On an Apple-silicon Mac it would need to be
loaded and validated inside the container engine's Linux VM; the Mac host does
not enforce this profile natively.

### Why Landlock is deferred

Landlock is not a Docker Compose `security_opt`. CyClaw would need a PID-1
launcher that probes the host Landlock ABI, builds the ruleset before opening
sensitive descriptors, fails closed when policy is mandatory, and then execs
Uvicorn. The needed Landlock syscalls must also remain allowed by seccomp, and
the restrictions inherit into `/ops` children. Adding and maintaining that
launcher is more code and portability risk than the current single-operator
threat justifies. AppArmor plus the read-only rootfs is the Stage 4 equivalent
for now. Revisit Landlock only for hosts without AppArmor or after services are
split by role.

## 4. Stage 5 - gVisor / Firecracker evaluation

### Comparison for CyClaw

| Area | gVisor (`runsc`) | Firecracker microVM |
|---|---|---|
| FastAPI/Python | Python runtimes are continuously tested; exact image still requires a real compatibility run. | Normal Linux guest compatibility; operator owns kernel, rootfs, init, and updates. |
| Torch CPU | Likely compatible; CPU-bound work generally sees less overhead. Benchmark imports, threading, and inference. | Native guest CPU behavior; application memory dominates. |
| Chroma/SQLite/BM25 | Likely functional, but syscall-heavy file I/O, mmap, locks, and persistence are likely the hot overhead/compatibility area. | Native guest filesystem semantics, but persistence requires managed block images or a guest service. |
| Ollama/LM Studio | Keep model/GPU outside the sandbox and connect through gVisor netstack; never use host networking. | Requires TAP/NAT or vsock proxying; host service must be exposed only to the microVM path. |
| CPU/memory/startup | Adds a userspace kernel and helper processes; CPU-bound paths may be modest, I/O/network paths slower. No CyClaw numbers exist—measure them. | VMM startup can be small, but published VMM-only numbers exclude guest kernel, Python, Torch, Chroma, and app memory. Total cold start is materially higher. |
| Single-operator operations | Moderate: install/patch one OCI runtime, select `runtime: runsc`, benchmark and debug compatibility. | High: KVM host, guest kernel/rootfs lifecycle, jailer, TAP/NAT/firewall, storage, updates, backups, supervision. |
| `real_repo_loop` | Git/worktree bind mounts can work but file-heavy operations may slow. A separate no-network executor remains preferable. | Ordinary host bind-mounted worktrees do not map cleanly; stage through a block image, network service, or vsock. |
| Telegram | Normal DNS/TCP/TLS through netstack; keep the channel as a separate role. | Normal guest egress after TAP/NAT policy; more machinery for no additional product value. |
| Apple silicon | Supports Linux ARM64, not a native macOS Docker Desktop custom-runtime guarantee. It must run in a controllable Linux VM. | Requires ARM64 Linux with `/dev/kvm`; a Mac host by itself is not a Firecracker deployment target. |
| Falco visibility | Host eBPF observes `runsc`, not a complete guest syscall stream; Falco 0.44 removed its gVisor engine. | Host Falco sees the VMM/TAP, not guest syscalls; guest monitoring is separate. |

There are no honest CyClaw-specific overhead numbers yet. The compatibility and
performance claims above are hypotheses bounded by upstream documentation;
only cold/warm query, persistence/recovery, Git worktree, and Telegram
benchmarks on the target host can promote them to facts.

### Recommendation

**Stage 5 is still not justified. Stop at a validated Stage 3 and enforced
AppArmor Stage 4 for the stated trusted-host, single-operator model.** If a
sandbox becomes mandatory, evaluate gVisor first. Firecracker is justified only
when a hardware-virtualized boundary is an explicit requirement and the
operator accepts a dedicated Linux KVM host plus guest-image/network/storage
operations.

Stage 5 becomes mandatory if any one of these becomes true:

- CyClaw intentionally executes code/tests from untrusted repositories or PRs.
- A remote caller or public Telegram bot can reach the executor.
- Multiple mutually untrusted operators share the host.
- The executor receives host credentials, SSH agents, the Docker socket, or
  broad host mounts.
- The service becomes internet-facing or makes a contractual isolation claim.
- Adversarial test workloads become a supported product feature.

No gVisor or Firecracker deployment artifact is shipped by this change because
those criteria are false. A future Linux-only gVisor evaluation would begin
with the following overlay while preserving host loopback publication:

```yaml
services:
  cyclaw:
    runtime: runsc
    ports:
      - "127.0.0.1:8787:8787"
```

```bash
sudo runsc install
sudo systemctl reload docker
docker run --rm --runtime=runsc hello-world
docker compose -f docker-compose.yml -f compose.gvisor-evaluation.yml up --build
```

If Stage 5 criteria become true, save the reviewed overlay above as
`compose.gvisor-evaluation.yml` before running that evaluation command; the file
is intentionally not shipped while Stage 5 remains unjustified.

Do not use `network_mode: host`. Keep Ollama outside the sandbox behind a
narrow proxy/private network. A Firecracker launch script is intentionally not
provided: on the current macOS/home-lab target it would be non-working theater,
not a deploy path.

### CyClaw code impact

Stages 3, 4, and a future whole-container gVisor evaluation are deploy-layer
changes. They require no change to `gate.py`, `graph.py`, or
`mcp_hybrid_server.py`. A future hostile executor should become a separate
optional `agentic` service with only its worktree mounted, `network_mode: none`,
no `HOME`/tokens/corpus/soul/config/Docker socket, and its own seccomp/AppArmor
policy. That split can remain outside the core request path and preserve I6.

## 5. eBPF detection to enforcement bridge

Falco remains an alerting layer. It should not be described as enforcement and
should not receive an automatic Docker-stop responder now; a responder with
Docker-socket/root authority creates a new privileged denial-of-service control
plane. The actual enforcement bridge is:

1. Use Falco/Inspektor Gadget to identify unexpected exec, filesystem, socket,
   mount, namespace, capability, `bpf`, `perf`, and ptrace behavior.
2. Reproduce and classify each event against a named workload action.
3. Block unnecessary syscalls in the reviewed seccomp profile.
4. Block filesystem/network behavior in AppArmor or a separate no-network
   executor container.
5. Keep Falco alerts as regression evidence after enforcement.

Immediate repository tuning adds `/app/index` to expected writes and `git` to
the monolithic optional-tool exec set. Optional `git`/`gh`/`rclone` network
connections are logged separately at `NOTICE`; they are not broadly allowed by
SaaS IP range. Python egress outside the configured local model endpoint stays
`WARNING` because the same process can represent either an explicit cloud-model
choice or hostile test code. Correlate it with CyClaw's audit event before
adding a narrow actor + action + destination exception.

When roles are split, label and maintain separate Falco rules for
`gate|agentic|sync|telegram|executor`:

- Gate: expected model destination only; shells and external tools are high
  severity.
- Agentic Git/planner: GitHub and configured model traffic, correlated to audit.
- Sync: rclone actions and configured remote only.
- Telegram: Telegram client traffic and loopback `/query` only.
- Executor: any network connection or read outside its worktree is critical.

For a future isolated executor worktree, prefer container-aware Inspektor
Gadget tools before custom bpftrace code:

```bash
sudo ig run ghcr.io/inspektor-gadget/gadget/trace_exec:latest \
  -c cyclaw-executor --paths -o json --timeout 600
sudo ig run ghcr.io/inspektor-gadget/gadget/trace_open:latest \
  -c cyclaw-executor -o json --timeout 600
sudo ig run ghcr.io/inspektor-gadget/gadget/trace_tcp:latest \
  -c cyclaw-executor --connect-only -o json --timeout 600
sudo ig run ghcr.io/inspektor-gadget/gadget/trace_dns:latest \
  -c cyclaw-executor -o json --timeout 600
```

Pin gadget image digests before operational use. Custom bpftrace is YAGNI until
these tools fail to answer a specific question.

## 6. Final deliverable package

This hardening increment contains:

- Updated Stage 3-5 and residual-risk text in `docs/THREAT_MODEL.md`.
- This canonical trace/evaluation procedure.
- `deploy/seccomp/README.md`; unsafe, untraced JSON profiles removed.
- `deploy/apparmor/cyclaw-gate` and an opt-in Compose overlay.
- Falco expected-path/process tuning and operator guidance.
- A static regression test for the deploy contract.

### Operator enablement checklist

Fail closed. Do not mark a stage complete from static parsing alone.

- [ ] Confirm the service publishes exactly `127.0.0.1:8787:8787`.
- [ ] Confirm Docker Engine 23.0 or newer and rendered
      `security_opt: [no-new-privileges:true, seccomp:builtin]`.
- [ ] Confirm `user: 1000:1000`, `read_only: true`, `cap_drop: ALL`,
      `no-new-privileges:true`, and resource limits remain rendered by
      `docker compose config`.
- [ ] Confirm no repository custom seccomp profile is wired before native trace
      and replay evidence exists.
- [ ] Record kernel, architecture, Docker/runtime version, and image digest.
- [ ] Trace cold/warm/error/shutdown behavior on every supported architecture.
- [ ] Review the candidate against the matching Moby default; preserve argument,
      capability, and architecture filters.
- [ ] Replay the complete workload and negative tests under the candidate.
- [ ] Compile AppArmor with `apparmor_parser -Q -W`; start in complain mode.
- [ ] Review every AppArmor denial; enforce only after gate workload replay.
- [ ] Prove writes outside the six carve-outs, shells, IPv4/IPv6 raw,
      packet/AF_ALG sockets, mount, and ptrace fail.
- [ ] Prove writes inside carve-outs, `/health`, cold/warm `/query`, Chroma
      recovery, audit, shutdown, and restart still work.
- [ ] Enable Falco only after accepting its privileged host-observation trade;
      validate rules and tune narrow actor + action + target exceptions.
- [ ] Keep one-command rollback to Docker's builtin seccomp and
      `docker-default` AppArmor profile.
- [ ] Re-run CyClaw invariant, isolation, config, and deploy-contract tests.
- [ ] Record what was not exercised; do not call a candidate production-ready.

### Invariant confirmation

These changes do not modify Python application code, graph topology, routing,
retrieval, soul governance, audit convergence, or model gates. They preserve
I1-I6. No `agentic`, `sync`, `harness`, `telegram`, or optional runtime import is
moved into `gate.py`, `graph.py`, or MCP. A future executor service split must
remain optional and communicate across an out-of-band process boundary.

## Primary references

- [Docker seccomp](https://docs.docker.com/engine/security/seccomp/)
- [Docker `seccomp=builtin` runtime option](https://docs.docker.com/reference/cli/docker/container/run/)
- [Docker Engine 23.0 `builtin` profile naming](https://docs.docker.com/engine/release-notes/23.0/)
- [Moby default seccomp profile](https://github.com/moby/profiles/blob/main/seccomp/default.json)
- [OCI seccomp eBPF hook](https://github.com/containers/oci-seccomp-bpf-hook)
- [Inspektor Gadget seccomp advisor](https://inspektor-gadget.io/docs/main/gadgets/advise_seccomp/)
- [strace manual](https://man7.org/linux/man-pages/man1/strace.1.html)
- [Docker AppArmor](https://docs.docker.com/engine/security/apparmor/)
- [Moby default AppArmor template](https://github.com/moby/profiles/blob/main/apparmor/template.go)
- [Linux Landlock](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html)
- [gVisor installation](https://gvisor.dev/docs/user_guide/install/)
- [gVisor compatibility](https://gvisor.dev/docs/user_guide/compatibility/)
- [gVisor production guidance](https://gvisor.dev/docs/user_guide/production/)
- [Firecracker getting started](https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md)
- [Falco 0.44 gVisor-engine removal](https://falco.org/blog/falco-0-44-0/)
- [Ollama container tags](https://hub.docker.com/r/ollama/ollama/tags)
- [Falco alert forwarding](https://falco.org/docs/concepts/outputs/forwarding/)
