# CyClaw macOS/Linux Coding Harness

A grok-build / kimi-code style local coding harness for macOS (Apple Silicon,
arm64) and Linux, bash or zsh. This is the POSIX-shell sibling of
`docs/HARNESS_POWERSHELL.md` — same harness, same invariants, same security
posture; only the install/launch glue differs. After setup, running `cyclaw`
in any new terminal starts the harness control plane (loopback only) and opens
the slash-command-driven console at `http://127.0.0.1:8790`.

The harness itself (`harness/`) is pure Python and carries no OS-specific code
in its request-handling path: no shell-string invocation, no Windows-only
process control, no platform-specific credential store. The only genuinely
platform-coupled surface is the three install/launch scripts — `powershell/`
for Windows, `macos/` for macOS/Linux — which is why this is a sibling script
tree rather than a shared abstraction layer.

## Install

```bash
# From a CyClaw clone:
bash ./macos/install-cyclaw.sh

# Or let the installer clone origin main itself -- just run the script.
# Options: --repo-path ~/src/CyClaw  --skip-python-deps  --no-profile-edit  --no-path-edit
```

The installer: creates `~/.CyClaw`, clones or links the repo, creates a venv
and installs dependencies (CPU torch first, then `requirements.txt -c
constraints.txt`, matching the documented trap-avoidance order), writes the
`cyclaw` shim, and adds a PATH entry plus a `cyclaw()` shell function to your
rc file (`~/.zshrc` on zsh, `~/.bash_profile`/`~/.bashrc` on bash — detected
from `$SHELL`). Target shells: bash (including macOS's stock 3.2) and zsh;
BSD userland is assumed on macOS — no GNU-only flags, no Homebrew dependency.

Uninstall (keeps data by default):

```bash
bash ./macos/uninstall-cyclaw.sh                 # remove PATH/rc-function hooks only
bash ./macos/uninstall-cyclaw.sh --remove-home   # also delete ~/.CyClaw (prompts)
```

`macos/invoke-cyclaw.sh` (the `cyclaw` shim's launcher) falls back to a
system `python3`/`python` on `PATH` when `~/.CyClaw/venv/bin/python` is
absent (e.g. after `install-cyclaw.sh --skip-python-deps`) — it only warns
if neither the venv nor a system Python is found. If the harness behaves
unexpectedly (wrong dependency versions, missing packages), check which
interpreter actually ran: a system Python without CyClaw's pinned deps will
silently answer `python -m harness.server` in place of the venv's.

## Home layout (`~/.CyClaw`)

Identical to the Windows layout described in `docs/HARNESS_POWERSHELL.md`
(`config.json`, `sessions/`, `skills/`, `tools/`, `memory/`, `repo/`, `venv/`,
`bin/`) — `harness/config.py::default_home()` already resolves `~/.CyClaw` on
any platform without `CYCLAW_HOME` set (it checks `CYCLAW_HOME`, then
`USERPROFILE` for the Windows case, then falls back to `Path.home()`, which is
the macOS/Linux case). `CYCLAW_HOME`, `CYCLAW_REPO`, and `CYCLAW_HARNESS_PORT`
override the same way on every platform; `CYCLAW_API_KEY` authenticates the
state-changing routes and a CSRF token embedded in the console page — passed
through from the caller's environment, never generated or written to disk by
the launcher.

## The console

Same slash commands, same routes, same behavior as
`docs/HARNESS_POWERSHELL.md` describes — the console and the FastAPI app
underneath it are the same Python code on every platform. See that doc's
"The console" and "Agentic coding runs" sections; nothing there is
Windows-specific.

## Security posture

Identical to `docs/HARNESS_POWERSHELL.md`'s "Security posture" section — the
loopback-only bind, the Bearer `CYCLAW_API_KEY` gate, the per-process CSRF
token, the `Origin`/`Sec-Fetch-Site` cross-site guard, the checks-profile
allow-list, and the `run_id`/branch-name validation are all enforced in
`harness/server.py`, which carries no platform branch. Two platform-specific
notes worth calling out:

- **Git credentials for `push`/`publish`.** `agentic/deepagent_github/repo_workspace.py`'s
  `push_branch()` authenticates via whatever OS-native git credential helper is
  configured in `HOME` — on macOS that's typically `git-credential-osxkeychain`
  (`gh auth setup-git` configures it), backed by the macOS Keychain instead of
  Windows' Credential Manager. CyClaw code itself never touches either store
  directly; it only passes `HOME` through and lets `git` resolve its own
  `credential.helper`.
- **Containment.** `agentic/fsconnect/pathsafe.py`'s `ScopedRoots` — the
  strongest containment primitive in this repo — is POSIX-first by
  construction (`openat`/`O_NOFOLLOW`/held `dir_fd`); Windows is the fallback
  branch. macOS takes the exact same code path as Linux here, not a weaker
  one. The `.git`-directory aliasing defenses in
  `agentic/deepagent_github/repo_workspace.py` also explicitly account for
  HFS+/APFS Unicode-format-character stripping, not just NTFS 8.3 names.

## torch on macOS: plain build, not the `+cpu`-suffixed one

**macOS installs plain `torch==2.13.0`.** No `+cpu` suffix, no `--index-url`
override. Apple Silicon has no separate CPU/CUDA build to disambiguate, so no
`+cpu`-suffixed macOS wheel is published at all — unlike Linux/Windows, where
that suffix exists specifically to avoid the default CUDA-bundled wheel.
Confirmed by this repo's first `macos-latest` CI run (the `+cpu` pin 404s
there), and independently against PyPI on 2026-08-02: `torch==2.13.0` publishes
six macOS wheels and every one is `macosx_14_0_arm64`.

Two consequences that follow from that platform tag, and are worth stating
because they bound who can run this at all:

- **macOS 14 (Sonoma) is a floor, not a preference.** macOS 13 and earlier
  cannot install the wheel.
- **Intel Macs are unsupported at this pin.** There is no `x86_64` macOS wheel
  for torch 2.13.0, so an Intel Mac cannot satisfy it.

*(History, kept because the reasoning is still instructive: while authoring
this port, `download.pytorch.org` was unreachable from the authoring
environment, so this was originally written as a suspicion — every other
compiled-extension dependency (`chromadb`, `onnxruntime`, `numpy`, `pandas`,
`psycopg-binary`) had been confirmed to publish an arm64/cp312 wheel, but that
one index could not be checked. CI then confirmed it.)*

`.github/workflows/ci.yml`'s `macos-latest` leg installs torch directly
(`pip install "torch==2.13.0"`, no index override) and then the rest of
`requirements.txt`/`constraints.txt` from copies with the `torch==`/
`--extra-index-url` lines stripped, so pip never tries to reconcile the
installed plain build against the `+cpu`-suffixed pin those manifests
otherwise hardcode for Linux/Windows. If installing the harness locally on
macOS by hand (not through the CI lane or `macos/install-cyclaw.sh`, which
both already do this correctly), use `pip install torch==2.13.0` before
`requirements.txt` rather than following `CLAUDE.md`'s Windows/Linux-oriented
`torch==2.13.0+cpu --index-url ...` install line verbatim.
