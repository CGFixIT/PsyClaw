# Docker Security Value 101

**Status:** operator education · claim-vs-reality · not a threat-model amendment
**Authority:** running code + `docs/THREAT_MODEL.md` + `docs/DOCKER.md` + `deploy/{seccomp,falco,apparmor}/` win if this file and those disagree.
**Audience:** operator who needs ELI5 first, then Tech101, then an honest map of what CyClaw actually ships.
**Date:** 2026-08-28

**One-line map:** containers are cheap apartments on a shared building (the host kernel). AppArmor is the lease. Seccomp is which buttons on the elevator you are allowed to press. Falco is the hallway camera that *records*, it does not lock doors. Firecracker/microVMs are “give that tenant their own tiny house with their own electrical panel.” CyClaw ships the apartment + lease + elevator limits + optional camera. It does **not** ship the tiny house. That is explicit in the threat model, not an oversight.

Grounded in [github.com/cgfixit/CyClaw](https://github.com/cgfixit/CyClaw). `github.com/cyclaw` is not this repo.

---

## ELI5

Imagine a big apartment building. The building’s plumbing, electricity, and front door are the **Linux kernel**. Everyone in the building uses the same pipes.

**A Docker container** is one apartment. You get your own furniture (files), your own mailbox number (network namespace), and a cap on how much water you can run (cgroups). You are *not* in a separate building. If someone finds a way to crawl through the shared plumbing, they can reach every apartment. That is a container escape. It is not theoretical. Shared kernel is the whole trade: cheap and dense, weaker isolation.

**AppArmor** is the lease taped to the door: “you may open the fridge and the closet; you may not touch the electrical panel or the neighbor’s unit.” It talks in *paths and actions* — this file, that capability, this kind of socket. Break the lease and the kernel says no.

**Seccomp** is a list of elevator buttons. Linux has ~300+ buttons (syscalls: “open a file,” “create a raw socket,” “load a kernel module,” “trace another process”). Seccomp says which buttons even work. Docker’s default profile already tapes over the most stupid-dangerous ones (`mount`, `ptrace`, `reboot`, `bpf`, `io_uring_*`, weird socket families). It is a blacklist of the worst ~44 calls, not a custom “this app only needs 17 calls” whitelist. A handmade 17-syscall whitelist is how you invent an app that cannot boot. CyClaw already made that mistake and deleted those profiles.

**Falco** is a hallway camera with a notebook. It watches what the apartment *actually did* (spawned a shell, wrote outside the allowed rooms, called the internet) and yells. It does not grab the tenant’s wrist. By the time Falco logs “shell spawned,” the shell already ran. Also: to see the building’s plumbing it needs a privileged sidecar with host-kernel access. That camera is itself a new attack surface, which is why CyClaw leaves it off unless you opt in.

**A microVM / Firecracker** is: tear out the shared plumbing. Give that tenant a tiny house with its own fuse box (guest kernel) sitting on a hardware-enforced lot line (KVM). If they blow up their own fuse box, your house still has power. That is the isolation you want when you run *strangers’ code* or *model-written tests you do not trust*. It costs more ops, needs KVM, and is overkill for “I run my own RAG server on a machine I own.” AWS built Firecracker for Lambda/Fargate — many strangers per host. That is not CyClaw’s deployment.

**Cousins you will hit in the same conversation:**

- **Namespaces** = “you cannot *see* the other apartments.”
- **Cgroups** = “you cannot *hog* the water and power.”
- **Capabilities** = fine-grained “adult keys” (bind port 80, load a module). `cap_drop: ALL` throws every key in the river.
- **SELinux** = AppArmor’s stricter, label-based cousin (Red Hat world). Same job, different language.
- **gVisor** = fake kernel in userspace that intercepts syscalls. Stronger than Docker, weaker/weirder than a real VM.
- **Kata Containers** = “run this OCI container inside a lightweight VM.” Not an isolation primitive itself; it *uses* Firecracker/Cloud Hypervisor/QEMU.
- **Landlock** = newer Linux LSM, path-based, no privileged profile load. CyClaw deferred it.
- **Seatbelt (`sandbox-exec`)** = macOS’s version of “lease + no network + stay in this folder.” CyClaw’s agentic executor uses this on Darwin.
- **Windows Job Object** = “if the parent dies, kill the whole process tree.” That is a kill boundary, **not** a network namespace. Sockets still work. CyClaw is honest about that.
- **`unshare --net`** = Linux “this child has no network namespace.” CyClaw’s Linux executor uses this. Fail closed if the binary is missing.

---

## Tech101

### Docker containers

A container is a process (or process tree) with:

| Primitive | What it isolates | What it does not |
|---|---|---|
| mount / PID / net / UTS / IPC / user namespaces | view of files, processes, network stack, hostname | the kernel itself |
| cgroups | CPU, memory, PIDs | malice, only starvation |
| capabilities | privileged operations | anything the remaining caps + kernel bugs allow |
| union FS (overlay) | image layers | host mounts you explicitly attach |

The process still issues syscalls into **the host kernel**. Isolation is a software check inside the same kernel the untrusted code is calling. One exploitable kernel bug removes namespaces, cgroups, seccomp, and AppArmor together. That is the load-bearing fact. Everything else is mitigation on top of it.

Docker defaults that actually matter:

- default seccomp profile on
- default AppArmor profile `docker-default` on Ubuntu/Debian hosts (not a thing on macOS; Docker Desktop’s Linux VM is the real kernel)
- **Kubernetes historically left default seccomp off** — cluster users who assume “it’s a container so it’s filtered” are often wrong

CyClaw compose adds the grown-up defaults on purpose: loopback publish `127.0.0.1:8787:8787`, `user: 1000:1000`, `cap_drop: ALL`, `no-new-privileges:true`, `read_only: true` + tmpfs `/tmp`, mem/pids/cpus caps, `seccomp:builtin`. Native Apple Silicon install is still the preferred path; GHCR is `linux/amd64` distribution, not the Mac story.

Brutal honesty: a hardened container is “harder to trip over.” It is not “safe to run hostile multi-tenant agent code.”

### Seccomp profiles

Seccomp-BPF attaches a filter to a process. Every syscall hits the filter before the kernel does real work.

- **Default-deny whitelist:** only the syscalls you named. Strong. Fragile. Miss `futex` or `exit_group` and Python dies in a way that looks like a security win and is actually a boot loop.
- **Default-allow blacklist:** Docker’s builtin. Blocks the cartoonishly dangerous calls, leaves the rest. Compatible. Not tight.

Argument filtering matters. A rule `socket: ALLOW` can silently drop the default profile’s “no AF_ALG / no AF_PACKET” restrictions. CyClaw’s `deploy/seccomp/README.md` exists because someone shipped name-only allowlists that could not boot the gate, and they were deleted rather than theater-kept. Stage 3 in the threat model is “trace the real image on amd64 *and* arm64, start from current Moby default, keep its argument/capability filters.” That procedure is written. The custom profile is **not** generated. Builtin is the honest policy.

Firecracker also uses seccomp — but on the **VMM process**, per-thread, so the hypervisor itself cannot call random host syscalls. Different layer: guest kernel mediates guest syscalls; host seccomp mediates the VMM.

### AppArmor

Linux Security Module. Path- and capability-oriented MAC.

- Lives in the host kernel (Ubuntu/Debian default; not macOS native).
- Docker applies `docker-default` unless you pass `--security-opt apparmor=...`.
- Complements seccomp: seccomp cannot see through a userspace pointer, so it cannot safely filter `socketcall(2)` arguments on 32-bit. AppArmor hooks `security_socket_create()` and can deny `AF_ALG` at the socket layer. That is why Moby moved some of that block from seccomp to AppArmor/SELinux. Different altitude, same fight.

CyClaw’s `deploy/apparmor/cyclaw-gate` is Stage 4: opt-in candidate. Writes only under the same six compose carve-outs (`data/index/logs/checkpoints/.emb_cache/tmp`). Intentionally omits shells, `git`, `gh`, `rclone`. Raw/packet/AF_ALG sockets denied. Not enabled by default. Not runtime-validated inside Docker Desktop’s VM. Complain-mode first, then enforce, then four negative tests must fail (write `/app/blocked`, raw ICMP, AF_PACKET, `sh -c true`). If you skip that replay you are LARPing confinement.

### Falco

Runtime detection. eBPF (modern probe, kernel ≳ 5.8) or older drivers. Reads syscalls from the host and matches rules.

CyClaw overlay (`deploy/falco/falco_rules.yaml`) watches five things: unexpected exec, any shell, write outside allowed roots, unexpected outbound, optional tool outbound (`git`/`gh`/`rclone`). Detection only. Never blocks.

Why it ships **disabled**: the sidecar is privileged with host `/proc`, tracing, and a Docker socket mount. `:ro` on the socket inode does **not** make the Docker API read-only. Giving an auto-responder that socket to “kill the bad container” is how you add a privileged DoS control plane. Leave it off unless you accept host-kernel access and will actually read the logs.

Image pin in-repo is `falcosecurity/falco:0.39.2` digest-pinned. Falco 0.44 dropped the gVisor engine; host eBPF cannot see inside a gVisor sandbox completely. Relevant only if you ever put Stage 5 under the gate.

### Firecracker and microVMs

Firecracker is a VMM. It uses KVM to run **microVMs**: a stripped device model, no PCI/USB/GPU zoo, ~5 MiB overhead, ~125 ms boot. Each guest gets its own kernel. Guest root is not host root. Production wrap: the **jailer** chroots the VMM, applies cgroup + namespace, drops privs, then Firecracker loads per-thread seccomp.

Use this when the threat is “code I do not control, on a host that also runs other people’s stuff.” That is Lambda. That is a public code-interpreter. That is “the model wrote `conftest.py` and pytest will import it.”

Do **not** use this because a blog said “AI agents need microVMs.” You still need: KVM on the host (or nested virt), an image/rootfs pipeline, networking/storage story, and an honest threat model. On a Mac, Firecracker is not “docker run but safer” — you are virtualizing inside Docker Desktop’s already-virtualized Linux. CyClaw’s threat model parks this at Stage 5: not justified for single-operator trusted host; mandatory if the executor is ever pointed at a repo or toolchain the operator did not configure.

Kata = OCI runtime that *puts the container in a VM*. Cloud Hypervisor = cousin VMM with more devices (GPU passthrough). gVisor = intercept syscalls in userspace (`runsc`). Different products, same question: is the kernel shared?

---

## Isolation ladder (what question each layer answers)

| Layer | Question it answers | Shared kernel? | CyClaw today |
|---|---|---|---|
| Process + drop caps | “does this run as root with every key?” | yes | yes — uid 1000, `cap_drop: ALL` |
| Namespaces + cgroups | “can it see/hog the rest of the machine?” | yes | yes — Docker |
| Seccomp | “which syscalls reach the kernel?” | yes | yes — `seccomp:builtin` only |
| AppArmor / SELinux / Landlock | “which files, sockets, caps even if the syscall is allowed?” | yes | scaffold only, off |
| Falco / auditd / Numbat | “did something weird already happen?” | n/a (observe) | Falco off-by-default; Numbat is app-level audit, not kernel |
| gVisor | “can we pretend to be the kernel in userspace?” | host kernel still there, narrower surface | not shipped |
| Firecracker / Kata / Cloud HV | “does this workload have its own kernel?” | no (guest kernel) | not shipped, Stage 5 parked |
| Full VM / hypervisor | same, heavier | no | Docker Desktop’s Linux VM is this *for the whole engine*, not per workload |
| Darwin Seatbelt / Win Job Object / `unshare --net` | platform-native child jail for the **agentic executor**, not the gate | mixed | shipped in `production_sandbox()` — fail closed if missing |

Threat-model self-score: **4/10 versus hostile-workload containment.** Proportionate for “I run my RAG server on a box I own.” Insufficient if you start selling “the agent can execute untrusted code safely.”

---

## What CyClaw actually uses (claim vs reality)

Application policy is still the primary boundary: RAG-first, topology=policy, triple-gate, audit, soul governance, I6 module isolation. Containers do not enforce I1–I6. A pretty Dockerfile with a sloppy graph is still a sloppy graph.

**Container/OS layer, enforced when you use compose/GHCR:**

- loopback publish, non-root, no-new-privileges, cap_drop ALL, read-only rootfs, resource caps, Docker builtin seccomp
- Ollama stays on the host; model weights are not baked into the image
- no secrets/corpus/index in layers

**Optional / not default:**

- Falco monitoring profile (privileged, detect-only)
- AppArmor `cyclaw-gate` overlay
- custom seccomp allowlist (procedure exists, artifact does not)

**Agentic executor jail (`production_sandbox()`, #1153/#1160) — different product surface from Docker:**

- Windows: Job Object, `KILL_ON_JOB_CLOSE`. Process tree dies with the job. **Sockets still work.** Do not write “no network” on Windows.
- macOS: `sandbox-exec` Seatbelt — deny network, writes limited to worktree + disposable `TMPDIR`
- Linux: `unshare --net`
- missing binary / EPERM → `HardSandboxUnavailable`. No silent `subprocess.run` fallback in production

**Explicit non-goals from `docs/THREAT_MODEL.md`:** no per-workload microVM, no defense against hostile local root, no internet-facing multi-tenant deployment, no “the confirmation checkbox was a human.” `user_confirmed_online` is a self-asserted field on an unauthenticated `/query` unless you turn auth on.

Do not tell an MSP “we run in Firecracker.” You do not. Do not tell them Falco “prevents escapes.” It logs. Do not tell them Docker + seccomp is “VM isolation.” It is not.

---

## Business / legal (the part people lie about)

Isolation tech is a **control**. It is not SOC 2, HIPAA, PCI, or a Georgia Bar ethics opinion. A shared-kernel container with a Falco sidecar produces *operational evidence* that you intended confinement. Auditors and clients will still ask: who can reach the port, what left the box, what the provider retained, and whether a kernel CVE takes everyone down. `/audit/summary` is not a certification. Telemetry-kill is a real sovereignty control; it is also not a network kill switch.

If you ever productize this for Atlanta dental/law: sell the *single-operator, loopback, audit-convergent, no-telemetry* posture. Do not sell Stage 5 language you did not ship. The first time an agentic executor runs a hostile `conftest.py` on a native Mac as the operator account, the blast radius is the operator’s home directory, not “the container.” Seatbelt on Darwin is the actual mitigation there, not Docker.

---

## When you would actually reach for the next rung

Stay on Docker + builtin seccomp + dropped caps if: one trusted operator, loopback, you control the image and the repo, agentic stays default-off.

Turn on Falco if: you will read the logs and accept a privileged sidecar. Otherwise it is noise plus attack surface.

Finish Stage 3 (traced seccomp) and Stage 4 (enforced AppArmor) if: you run the GHCR image on a real Linux host as the long-term path and want the container to fail closed on “shell appeared” / “wrote `/etc`.”

Reach for Firecracker/gVisor/Kata if and only if: the executor runs code you did not pick, for a tenant you do not trust, on a host that holds other tenants’ data. That is a threat-model amendment, not a compose flag. CyClaw’s own docs say do not add that wiring without that amendment and Stage-3-quality evidence.

The common lie in 2026 agent-sandbox marketing is collapsing all of the above into the word “sandboxed.” A process with a timeout is not a sandbox. A container is a sandbox of one strength. A microVM is another. CyClaw uses the first two (and platform child jails for the executor) and documents the third as parked. Keep the words honest and the architecture stays a moat. Inflate them and it becomes a career-signal problem the first time someone who knows KVM reads the README.

---

## Related files

| Path | Role |
|---|---|
| `docs/THREAT_MODEL.md` | Authoritative scope, including Stage 5 parked |
| `docs/DOCKER.md` | GHCR operator runbook |
| `docs/SECCOMP_EBPF_HARDENING.md` | Stage 3 capture procedure |
| `deploy/seccomp/README.md` | Builtin enforced; custom profile not generated |
| `deploy/falco/README.md` | Opt-in detection sidecar |
| `deploy/apparmor/README.md` | Opt-in Stage 4 profile |
