#!/usr/bin/env python3
# Environment-dependency drift checks that live OUTSIDE the four pin manifests.
#
# dep-guard answers "do the manifests agree with each other." extract_pins.py
# adds "does requirements.txt agree with constraints.txt." Both stop at the
# manifest boundary. This adds the surfaces where an environment dependency is
# real, load-bearing, and recorded NOWHERE a manifest checker looks:
#
#   E1  a tool version pinned inline in a workflow file (flake8, WPS, pip,
#       actionlint, zizmor) -- CI-gating versions with no manifest record
#   E2  the Python version, declared independently in four places
#   E3  a third-party module imported directly by source but declared in no
#       manifest -- the class dep-guard structurally cannot see, because it
#       reads manifests and never reads imports
#   E4  the install-surface SCOPE contract (which surface may carry extras)
#   E5  the Docker build's dependency-install contract, including the
#       fallback torch pre-install held in lock-step with constraints.txt
#   E6  the rest of the Docker surface -- docker-compose.yml, .dockerignore,
#       and publish-ghcr.yml -- agreeing with the Dockerfile and each other
#
# Pure stdlib, no network, no install required -- same constraints dep-guard
# and extract_pins.py hold, so this runs in a fresh clone before pip does.

from __future__ import annotations

import ast
import fnmatch
import re
import sys
from pathlib import Path

# --repo-root retargets every check at another tree, which is what makes the
# mutation self-tests in verify.sh possible without touching the real repo.
if "--repo-root" in sys.argv:
    REPO = Path(sys.argv[sys.argv.index("--repo-root") + 1]).resolve()
else:
    REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"

failures: list[str] = []
warnings: list[str] = []


def fail(code: str, msg: str) -> None:
    failures.append(f"[{code}] {msg}")
    print(f"  FAIL  [{code}] {msg}")


def warn(code: str, msg: str) -> None:
    warnings.append(f"[{code}] {msg}")
    print(f"  warn  [{code}] {msg}")


def ok(code: str, msg: str) -> None:
    print(f"  ok    [{code}] {msg}")


def info(code: str, msg: str) -> None:
    print(f"  info  [{code}] {msg}")


# --- E1: tool versions pinned inline in workflows -----------------------------
# These gate CI but appear in no manifest, so nothing cross-checks them. The
# risk is not a wrong version -- it is the SAME tool pinned at two different
# versions in two jobs, which silently makes one lane's result unreproducible
# against the other's. flake8/WPS are the sharpest case: they gate the lint
# lane, and a version skew there changes which findings a PR must waive.
_WORKFLOW_TOOLS = ("flake8", "wemake-python-styleguide", "actionlint-py", "zizmor", "pip", "ruff", "mypy")
_PIN_RE = re.compile(rf"\b({'|'.join(map(re.escape, _WORKFLOW_TOOLS))})==([0-9][0-9a-zA-Z.\-]*)")


def check_workflow_tool_pins() -> None:
    print("E1 workflow-pinned tool versions are internally consistent")
    if not WORKFLOWS.is_dir():
        warn("E1", f"no workflow directory at {WORKFLOWS}")
        return
    seen: dict[str, dict[str, list[str]]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            for tool, version in _PIN_RE.findall(line):
                seen.setdefault(tool, {}).setdefault(version, []).append(f"{wf.name}:{lineno}")
    if not seen:
        info("E1", "no inline tool pins found in workflows")
        return
    for tool, versions in sorted(seen.items()):
        sites = sum(len(v) for v in versions.values())
        if len(versions) > 1:
            detail = "; ".join(f"{v} at {', '.join(s)}" for v, s in sorted(versions.items()))
            fail("E1", f"{tool} pinned at {len(versions)} different versions across workflows: {detail}")
        elif sites > 1:
            version = next(iter(versions))
            ok("E1", f"{tool}=={version} consistent across {sites} sites ({', '.join(versions[version][:3])}...)")
        else:
            version = next(iter(versions))
            ok("E1", f"{tool}=={version} ({versions[version][0]})")


# --- E2: the Python version, declared in four independent places --------------
def check_python_version() -> None:
    print("E2 Python version agrees across every surface that declares one")
    found: dict[str, str] = {}

    pyproject = REPO / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', pyproject.read_text(encoding="utf-8"))
        if m:
            found["pyproject requires-python"] = m.group(1)

    dockerfile = REPO / "Dockerfile"
    if dockerfile.is_file():
        m = re.search(r"(?m)^FROM\s+python:(\d+\.\d+)", dockerfile.read_text(encoding="utf-8"))
        if m:
            found["Dockerfile FROM"] = m.group(1)

    env = REPO / "environment.yml"
    if env.is_file():
        m = re.search(r"(?m)^\s*-\s*python\s*=\s*(\d+\.\d+)", env.read_text(encoding="utf-8"))
        if m:
            found["environment.yml"] = m.group(1)

    wf_versions: set[str] = set()
    if WORKFLOWS.is_dir():
        for wf in sorted(WORKFLOWS.glob("*.y*ml")):
            for m in re.finditer(r"""python-version:\s*["']?(\d+\.\d+)""", wf.read_text(encoding="utf-8")):
                wf_versions.add(m.group(1))

    # Compare the concrete minor versions. pyproject's is a RANGE (">=3.12"),
    # so it is checked for compatibility rather than string equality -- a
    # ">=3.12" that every other surface satisfies is correct, not drift.
    concrete = {k: v for k, v in found.items() if k != "pyproject requires-python"} | {
        f"workflow python-version[{v}]": v for v in sorted(wf_versions)
    }
    distinct = set(concrete.values())
    if len(distinct) > 1:
        fail("E2", f"Python version disagrees across surfaces: {concrete}")
    elif distinct:
        pinned = next(iter(distinct))
        spec = found.get("pyproject requires-python", "")
        ok("E2", f"every surface targets Python {pinned} (pyproject: {spec or 'unspecified'})")
        floor = re.search(r"(\d+)\.(\d+)", spec)
        if floor and tuple(map(int, pinned.split("."))) < (int(floor.group(1)), int(floor.group(2))):
            fail("E2", f"surfaces target {pinned}, below pyproject's floor {spec}")
    else:
        warn("E2", "no concrete Python version found on any surface")


# --- E3: a direct import declared in no manifest -------------------------------
# The gap dep-guard cannot see by construction: it reads manifests, never
# imports. Found in practice (2026-08-02 audit) -- huggingface_hub and
# starlette are imported directly by source and declared nowhere, surviving
# only as hard transitives of sentence-transformers and fastapi. That works
# until the parent drops them.
#
# Intentionally-undeclared modules go here WITH the reason, so the allowlist
# stays an argued exception list rather than a silencer.
_IMPORT_ALLOWLIST = {
    # Lazy-imported operator-supplied driver: agentic/sqlconnect/client.py
    # imports it inside a function and raises a friendly "pyodbc is not
    # installed (pip install pyodbc)" if absent, so a disabled connector needs
    # nothing installed. Declaring it would install an MSSQL driver on every
    # box for a connector that ships disabled.
    "pyodbc",
    # Lazy in-function import at utils/onnx_telemetry.py's suppression seam
    # (issue #1135): onnxruntime is DELIBERATELY undeclared -- it arrives only
    # as a transitive of chromadb (and of fastembed under the guardrails
    # extra), the helper is a getattr-guarded no-op when it is absent, and its
    # unbounded-transitive status is a standing recorded review finding in
    # .claude/skills/otel-hardening/check_otel.py (T13). Declaring a pin here
    # would be a dependency-policy change that finding exists to make
    # deliberate, not a lint fix.
    "onnxruntime",
}
_FIRST_PARTY = {
    "utils", "retrieval", "llm", "schemas", "sync", "agentic", "guardrails", "harness", "telegram",
    "opentweet",
    "gate", "gate_ops", "gate_auth", "gate_memory", "graph", "mcp_hybrid_server", "metrics",
    "memory", "tests", "conftest",
}
# import name -> PyPI distribution name, for the cases where they differ by more
# than punctuation. The underscore/hyphen cases (rank_bm25, langchain_xai,
# huggingface_hub, ...) are NOT listed here on purpose -- PEP 503 treats "_" and
# "-" as the same character, so those are handled by normalization below rather
# than by a hand-maintained table that would silently rot.
_DIST_ALIAS = {
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    # utils/numbat_cel.py lazily imports celpy (distribution cel-python)
    # behind the optional `numbat-cel` extra; PEP 503 normalization does not
    # bridge the module/distribution name gap, so map it explicitly.
    "celpy": "cel-python",
}
# Only first-party runtime source is in scope. Skipping by name alone is
# fragile -- the venv in this tree was once ".venv312", not ".venv" -- so the rule is
# structural: any hidden directory, any build output, and any directory that
# IS a virtualenv (identified by its own pyvenv.cfg, whatever it is named).
_SKIP_DIRS = ("tests/", "docs/", "build/", "dist/", "site-packages/")


def _skipped(rel: str) -> bool:
    parts = rel.split("/")[:-1]
    if any(p.startswith(".") for p in parts):
        return True
    if "site-packages" in parts or "node_modules" in parts:
        return True
    return any(rel.startswith(d) for d in _SKIP_DIRS)


def check_undeclared_imports() -> None:
    print("E3 every third-party module imported by source is declared somewhere")
    manifests = {
        name: (REPO / name).read_text(encoding="utf-8").lower()
        for name in (
            "requirements.txt",
            "requirements-test.txt",
            "constraints.txt",
            "pyproject.toml",
            "environment.yml",
        )
        if (REPO / name).is_file()
    }
    if not manifests:
        warn("E3", "no manifests found to check against")
        return

    stdlib = set(sys.stdlib_module_names)
    venv_roots = {p.parent for p in REPO.rglob("pyvenv.cfg")}
    imported: dict[str, list[str]] = {}
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if _skipped(rel) or any(v in path.parents for v in venv_roots):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.setdefault(a.name.split(".")[0], []).append(rel)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.setdefault(node.module.split(".")[0], []).append(rel)

    undeclared: list[tuple[str, str, str]] = []
    for mod in sorted(imported):
        if mod in stdlib or mod in _FIRST_PARTY or mod in _IMPORT_ALLOWLIST:
            continue
        dist = _DIST_ALIAS.get(mod, mod)
        # PEP 503: "_" and "-" are equivalent in a distribution name, so accept
        # either spelling rather than guessing which one a manifest used.
        stem = re.escape(dist).replace("_", "[-_]").replace(r"\-", "[-_]")
        pattern = re.compile(rf"(?mi)^\s*[-\"']?{stem}\b")
        if not any(pattern.search(text) for text in manifests.values()):
            undeclared.append((mod, dist, imported[mod][0]))

    if undeclared:
        for mod, dist, where in undeclared:
            warn("E3", f"'{mod}' (dist: {dist}) imported by {where} but declared in no manifest "
                       f"-- relies on being a transitive of something else")
    else:
        ok("E3", f"all {len(imported)} imported modules resolve to stdlib, first-party, a manifest, or the allowlist")


# --- E4: the install-surface scope contract ------------------------------------
# The distinction a reviewer most often gets wrong, and the reason a correct
# tree can look like drift: constraints.txt pins packages that NO install
# surface installs by default. It is a version ceiling, not an install list.
_EXTRA_ONLY_MARKERS = ("deepagents", "nemoguardrails", "psycopg", "pgvector",
                       "langchain-openai", "langchain-anthropic", "langchain-xai")


def check_install_surface_scope() -> None:
    print("E4 install-surface scope contract (which surface may carry extras)")
    surfaces = {
        "requirements.txt": REPO / "requirements.txt",
        "requirements-test.txt": REPO / "requirements-test.txt",
    }
    missing = [name for name, path in surfaces.items() if not path.is_file()]
    if missing:
        fail("E4", f"required install surface(s) missing: {missing} -- runtime and "
                   "test tools are split; both manifests must exist")
        return
    leaked_any = False
    for name, path in surfaces.items():
        text = path.read_text(encoding="utf-8")
        # Only real requirement lines -- a package named in a comment (the file
        # documents the postgres extras in prose) is not an install.
        lines = [ln.split("#")[0].strip().lower() for ln in text.splitlines()]
        leaked = [m for m in _EXTRA_ONLY_MARKERS
                  if any(re.match(rf"^{re.escape(m)}\b", ln) for ln in lines if ln)]
        if leaked:
            leaked_any = True
            fail("E4", f"{name} installs extras-only package(s) {leaked} -- runtime "
                       f"and test manifests are the BASE surface; extras belong to "
                       f"pyproject.toml, pinned in constraints.txt")
    if not leaked_any:
        ok("E4", "requirements.txt is runtime-only and requirements-test.txt is "
           "test tools only (no extras) -- as designed")
    info("E4", "constraints.txt pins extras/transitives NO surface installs by default; that is a "
               "version ceiling, not drift")


# --- E5: Docker consumes the legacy constrained install surface ----------------
def check_docker_install_contract() -> None:
    """Keep Docker on the documented requirements.txt + constraints.txt path."""
    print("E5 Docker build uses the constrained legacy install surface")
    dockerfile = REPO / "Dockerfile"
    if not dockerfile.is_file():
        fail("E5", "Dockerfile not found; CyClaw's container install surface is unverifiable")
        return

    text = dockerfile.read_text(encoding="utf-8")
    required = {
        "copies dependency manifests": "COPY pyproject.toml constraints.txt requirements.txt ./",
        "uses constrained uv install": "uv pip install --system --no-cache-dir -r requirements.txt -c constraints.txt",
        "uses constrained pip fallback": "pip install --no-cache-dir -r requirements.txt -c constraints.txt",
    }
    missing = [label for label, fragment in required.items() if fragment not in text]
    cpu_torch = re.search(
        r"pip\s+install\s+--no-cache-dir\s+torch==(\S+)\s+--index-url\s+"
        r"https://download\.pytorch\.org/whl/cpu",
        text,
    )
    if not cpu_torch:
        missing.append("installs fallback CPU torch from the PyTorch CPU index")
    if missing:
        fail("E5", "Dockerfile dependency contract missing: " + "; ".join(missing))
        return
    if re.search(r"requirements-test\.txt", text):
        fail("E5", "Dockerfile must not copy or install requirements-test.txt -- "
                   "the production image stays test-tool-free")
        return
    ok("E5", "Docker copies manifests and uses requirements.txt + constraints.txt in both install paths")

    # The fallback's explicit torch pre-install and constraints.txt's torch pin
    # must move together. The Dockerfile's own comment records the miss this
    # guards: constraints moved 2.12.1 -> 2.13.0, the fallback line stayed
    # behind, and the fallback path installed the old wheel and then failed
    # the constrained resolve. A check that only asks "is there some
    # torch==" is exactly the check that passed that tree.
    constraints = REPO / "constraints.txt"
    if not constraints.is_file():
        info("E5", "no constraints.txt beside the Dockerfile; torch lock-step not checked")
        return
    pin = re.search(r"(?m)^torch==(\S+)", constraints.read_text(encoding="utf-8"))
    if not pin:
        fail("E5", "constraints.txt carries no torch== pin to hold the Dockerfile fallback to")
    elif pin.group(1) != cpu_torch.group(1):
        fail("E5", f"Dockerfile fallback pre-installs torch=={cpu_torch.group(1)} but constraints.txt "
                   f"pins torch=={pin.group(1)} -- keep the two in lock-step on every torch bump")
    else:
        ok("E5", f"Dockerfile fallback torch=={pin.group(1)} matches the constraints.txt pin")


# --- E6: the rest of the Docker surface must agree with the Dockerfile --------
# The Dockerfile is the install; docker-compose.yml is how the image runs,
# .dockerignore is what the build may see, and publish-ghcr.yml is how the
# image reaches the registry the compose file pulls from. Each is edited on
# its own and nothing cross-checks them. The failures this catches are quiet
# ones: a runtime-state directory dropped from .dockerignore bakes private
# corpus vectors into a published image; a mount dropped from compose leaves
# /query with no index under the read-only root fs (503, healthcheck green);
# a version bump that skips the compose default makes `docker compose pull`
# fetch the previous release beside newer source.
#
# The runtime-state directories .dockerignore keeps out of image layers and
# docker-compose.yml must therefore mount back in. "data" must be ignored as
# the directory itself (`data` or `data/`). A nested path such as
# `data/personality/` does not cover `data/corpus/` and must not green this
# check. ".emb_cache" is the named volume behind models.embeddings.cache_dir.
_RUNTIME_STATE_DIRS = ("logs", "checkpoints", "index", "data", ".emb_cache")
# The build stage COPYs exactly these before installing (E5's first fragment).
_COPIED_MANIFESTS = ("pyproject.toml", "constraints.txt", "requirements.txt")


def _ignore_patterns(dockerignore: Path) -> list[str]:
    lines = (ln.strip() for ln in dockerignore.read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and not ln.startswith(("#", "!"))]


def check_docker_surface_coherence() -> None:
    print("E6 docker-compose.yml, .dockerignore, and publish-ghcr.yml agree with the Dockerfile")
    dockerfile = REPO / "Dockerfile"
    compose = REPO / "docker-compose.yml"
    dockerignore = REPO / ".dockerignore"
    publish = WORKFLOWS / "publish-ghcr.yml"
    if not (compose.is_file() or dockerignore.is_file() or publish.is_file()):
        info("E6", "no docker-compose.yml, .dockerignore, or publish-ghcr.yml -- the Docker surface is the "
                   "Dockerfile alone")
        return
    docker_text = dockerfile.read_text(encoding="utf-8") if dockerfile.is_file() else ""

    if dockerignore.is_file():
        patterns = _ignore_patterns(dockerignore)
        excluded = [m for m in _COPIED_MANIFESTS if any(fnmatch.fnmatch(m, p.strip("/")) for p in patterns)]
        if excluded:
            fail("E6", f".dockerignore excludes {excluded}, which the Dockerfile COPYs before installing")
        else:
            ok("E6", ".dockerignore keeps the three COPYed manifests in the build context")
        not_ignored = [
            d
            for d in _RUNTIME_STATE_DIRS
            if not any(p.strip("/") == d for p in patterns)
        ]
        if not_ignored:
            fail("E6", f".dockerignore no longer excludes runtime state {not_ignored} -- index, personality, "
                       f"and agentic state must never enter image layers")
        else:
            ok("E6", f".dockerignore excludes every runtime-state directory {list(_RUNTIME_STATE_DIRS)}")
    else:
        info("E6", "no .dockerignore; build-context checks skipped")

    image = None
    if compose.is_file():
        compose_text = compose.read_text(encoding="utf-8")
        # Host exposure stays loopback: the container binds 0.0.0.0 so
        # docker-proxy can reach uvicorn, and the loopback invariant lives at
        # this host boundary (Dockerfile CMD comment, docs/THREAT_MODEL.md).
        publishes = re.findall(r'(?m)^\s*-\s*"?((?:[\d.]+:)?)(\d+):(\d+)"?\s*$', compose_text)
        if not publishes:
            fail("E6", "docker-compose.yml publishes no port -- the loopback publish is how the host reaches uvicorn")
        for host, host_port, _ in publishes:
            if host != "127.0.0.1:":
                fail("E6", f"docker-compose.yml publishes {host or '0.0.0.0:'}{host_port} -- host exposure must stay "
                           f"127.0.0.1 (loopback invariant)")
        expose = re.search(r"(?m)^EXPOSE\s+(\d+)", docker_text)
        cmd_port = re.search(r'"--port",\s*"(\d+)"', docker_text)
        # Absence must fail before comparing present values: dropping None from
        # the set used to green when EXPOSE/compose agreed but CMD --port was
        # gone, and uvicorn then silently listened on 8000. fail() records and
        # continues, so skip the comparison when either declaration is missing.
        absent = [name for name, hit in (("EXPOSE", expose), ("CMD --port", cmd_port)) if not hit]
        if absent:
            fail("E6", f"Dockerfile is missing {' and '.join(absent)} -- uvicorn defaults to 8000 "
                       f"without an explicit --port")
        else:
            ports = {
                "Dockerfile EXPOSE": expose.group(1),
                "Dockerfile CMD --port": cmd_port.group(1),
            } | {f"compose publish[{i}]": container for i, (_, _, container) in enumerate(publishes)}
            distinct = set(ports.values())
            if len(distinct) > 1:
                fail("E6", f"container port disagrees across the Docker surface: {ports}")
            elif publishes and all(h == "127.0.0.1:" for h, _, _ in publishes):
                ok("E6", f"loopback publish; container port {next(iter(distinct))} "
                         f"agrees across EXPOSE, CMD, and compose")

        unmounted = [d for d in _RUNTIME_STATE_DIRS
                     if not re.search(rf"(?m):/app/{re.escape(d)}(?::|\s|$)", compose_text)]
        if unmounted:
            fail("E6", f"docker-compose.yml mounts nothing at /app/{{{', '.join(unmounted)}}} -- .dockerignore "
                       f"keeps these out of the image, so an unmounted one is simply absent at runtime")
        else:
            ok("E6", "docker-compose.yml mounts every runtime-state directory .dockerignore excludes")

        image = re.search(r"(?m)^\s*image:\s*(\S+?):\$\{CYCLAW_IMAGE_TAG:-([^}]+)\}", compose_text)
        pyproject = REPO / "pyproject.toml"
        version = (re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
                   if pyproject.is_file() else None)
        if not image:
            fail("E6", "docker-compose.yml image: is not the documented `<name>:${CYCLAW_IMAGE_TAG:-<default>}` "
                       "form (docs/DOCKER.md)")
        elif version and image.group(2) != version.group(1):
            fail("E6", f"docker-compose.yml default CYCLAW_IMAGE_TAG is {image.group(2)} but pyproject.toml "
                       f"version is {version.group(1)} -- `docker compose pull` would fetch the previous release")
        elif version:
            ok("E6", f"docker-compose.yml default CYCLAW_IMAGE_TAG {image.group(2)} matches pyproject.toml")
    else:
        info("E6", "no docker-compose.yml; runtime-mount, port, and image-tag checks skipped")

    if publish.is_file():
        ptext = publish.read_text(encoding="utf-8")
        build_file = re.search(r"(?m)^\s*file:\s*(\S+)", ptext)
        if build_file and build_file.group(1).lstrip("./") != "Dockerfile":
            fail("E6", f"publish-ghcr.yml builds {build_file.group(1)}, not the repo Dockerfile E5 verifies")
        image_name = re.search(r"(?m)^\s*IMAGE_NAME:\s*(\S+)", ptext)
        if not image_name:
            warn("E6", "publish-ghcr.yml declares no IMAGE_NAME; cannot tie it to docker-compose.yml's image")
        elif image and image_name.group(1) != image.group(1):
            fail("E6", f"publish-ghcr.yml pushes {image_name.group(1)} but docker-compose.yml pulls "
                       f"{image.group(1)} -- the published image would never be the one compose runs")
        elif image:
            ok("E6", f"publish-ghcr.yml pushes the image docker-compose.yml pulls ({image_name.group(1)})")
    else:
        info("E6", "no publish-ghcr.yml; registry-name check skipped")


def main() -> int:
    print("== verify-deps: environment-dependency drift (outside the pin manifests) ==")
    check_workflow_tool_pins()
    check_python_version()
    check_undeclared_imports()
    check_install_surface_scope()
    check_docker_install_contract()
    check_docker_surface_coherence()
    print()
    print(f"{len(failures)} failure(s), {len(warnings)} warning(s)")
    if "--strict" in sys.argv and warnings and not failures:
        print("(--strict: warnings count as failures)")
        return 2
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
