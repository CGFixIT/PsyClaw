# CyClaw seccomp profiles

**Status: Docker builtin enforced; custom Stage 3 profile not yet generated.**

`docker-compose.yml` explicitly selects `seccomp:builtin`. That keeps Docker's
engine-maintained builtin policy active even if the daemon-wide default was
customized or set to unconfined. This explicit `builtin` selector requires
Docker Engine 23.0 or newer. The three former repository profiles were removed
because they were not produced from runtime traces:

- `gate-seccomp.json` allowed only 17 x86-64 syscalls and could not boot the app.
- `sync-rclone.json` was wired to the gate but omitted `listen`, `accept*`,
  `exit_group`, clocks, thread setup, and other normal Python/Uvicorn calls.
- `rclone-seccomp.json` contained invalid pseudo-syscall names such as
  `mount?no`.

Do not hand-author a replacement or deploy a trace tool's name-only allowlist.
Trace the exact image on every supported Linux architecture, start from the
matching current Moby/Docker default, and preserve its architecture,
capability, and argument filters. In particular, a generic `socket: ALLOW`
rule can discard address-family restrictions from the maintained default.

The capture, workload-replay, review, integration, and rollback procedure is
in [`docs/SECCOMP_EBPF_HARDENING.md`](../../docs/SECCOMP_EBPF_HARDENING.md).
Only commit `gate-seccomp.json` after that checklist passes on native Linux
`amd64` and `arm64`; until then, Docker's builtin is the safer enforced policy.
