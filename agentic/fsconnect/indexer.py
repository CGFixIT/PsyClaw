"""Toggleable RAG-corpus indexing of the file-share (decoupled, dry-run default).

Indexes ``index_root`` (the file-share, default the first writable root) into
CyClaw's corpus so a generate->write->index loop is possible: the local LLM
generates content, the operator writes it to the share, then -- if ``index_enabled``
is set -- this stages eligible files into the corpus and signals a reindex.

Decoupled to preserve isolation: it enumerates ``index_root`` via the ``pathsafe``
security core, stages eligible files into a corpus subdirectory, and (optionally)
triggers the existing indexer as a SUBPROCESS (``python -m retrieval.indexer``) --
it never imports retrieval/gate/graph/mcp, mirroring how ``sync/`` signals reindex.

Untrusted file content entering the corpus is a real trust boundary, so content is
advisory-injection-scanned during staging (flag surfaced; roadmap: quarantine).

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # noqa: S404 -- argv-list reindex trigger only; never shell=True
import sys
from collections.abc import Callable
from pathlib import Path

from agentic.fsconnect.client import build_injection_patterns
from agentic.fsconnect.config import FsConnectConfig
from agentic.fsconnect.pathsafe import ScopedRoots, split_components
from utils.errors import FsConnectError, FsConnectRuntimeError
from utils.logger import audit_log

_DEFAULT_STAGING = Path("data/corpus/fsconnect")
# Skip-cache for incremental apply(), kept beside the staged files so the cache
# and the staging dir are always consistent: clearing staging drops the cache too,
# forcing a full re-stage. A dotfile + non-corpus extension, so the retrieval
# indexer (corpus.extensions = .md/.txt) never ingests it.
_CACHE_NAME = ".fsindex_cache.json"


class FsIndexer:
    """Stages eligible file-share content into the corpus and signals reindex."""

    def __init__(self, cfg: dict, fs_cfg: FsConnectConfig, config_path: str = "config.yaml") -> None:
        self.cfg = cfg
        self.fs_cfg = fs_cfg
        self.config_path = config_path
        if not fs_cfg.index_root:
            raise FsConnectError("fsconnect.index_root is not set", code="FSCONNECT_INDEX_ERROR")
        self.index_root = fs_cfg.index_root
        self._patterns = build_injection_patterns(cfg) if fs_cfg.scan_content else []

    def _scan(self, data: bytes) -> int:
        if not self._patterns:
            return 0
        text = data.decode("utf-8", errors="replace")
        return sum(1 for _src, pat in self._patterns if pat.search(text))

    def _walk(
        self,
        roots: ScopedRoots,
        rel: str,
        manifest: list[dict],
        on_file: Callable[[str, bytes], None] | None = None,
        cached_entry_for: Callable[[str, dict], dict | None] | None = None,
    ) -> None:
        for entry in roots.list_dir(rel):
            name = entry["name"]
            child = f"{rel}/{name}" if rel else name
            if entry["type"] == "dir":
                self._walk(roots, child, manifest, on_file, cached_entry_for)
            elif entry["type"] == "file":
                ext = os.path.splitext(name)[1].lower()
                if ext not in self.fs_cfg.index_extensions:
                    continue
                if entry["size"] > self.fs_cfg.index_max_file_bytes:
                    manifest.append({"path": child, "size": entry["size"], "skipped": "too_large"})
                    continue
                # Incremental: if size+mtime match the prior run AND the staged
                # copy still exists, reuse the cached manifest entry and skip the
                # read+hash+stage entirely. cached_entry_for returns None to force
                # a fresh read (new/changed file, or staged copy missing).
                if cached_entry_for is not None:
                    hit = cached_entry_for(child, entry)
                    if hit is not None:
                        manifest.append(hit)
                        continue
                try:
                    data = roots.read_bytes(child, max_bytes=self.fs_cfg.index_max_file_bytes)
                except (FsConnectError, OSError) as exc:
                    # Isolate per-file read failures so one unreadable file never
                    # aborts the whole scan/apply. The size check above uses the
                    # (possibly stale) directory-listing size, so a file that grows
                    # past the cap between list_dir and read -- or that vanished,
                    # lost read permission, or turned into a non-regular file (a
                    # TOCTOU race) -- raises here. Quarantine it in the manifest and
                    # keep walking rather than failing the run for every other file.
                    manifest.append({"path": child, "size": entry["size"],
                                     "skipped": "read_error", "error": type(exc).__name__})
                    continue
                digest = hashlib.sha256(data).hexdigest()  # hash of file CONTENT, not metadata
                manifest.append({
                    "path": child, "size": entry["size"], "mtime": entry["mtime"],
                    "sha256": digest,
                    "injection_flag_count": self._scan(data),
                })
                # When apply() drives the walk it passes a staging callback so the
                # bytes we just read are written out in the SAME pass. Previously
                # apply() walked to build the manifest (reading every eligible file)
                # and THEN re-read each eligible file from disk a second time -- a
                # second per-component O_NOFOLLOW descent + full read -- purely to
                # fetch bytes the walk had already loaded. One read per file now.
                if on_file is not None:
                    on_file(child, data)

    def scan(self) -> dict:
        """Dry-run: enumerate eligible files under index_root (no copy, no reindex)."""
        manifest: list[dict] = []
        with ScopedRoots([self.index_root], create=True, allow_unc=self.fs_cfg.allow_unc_roots) as roots:
            self._walk(roots, "", manifest)
        eligible = [m for m in manifest if "skipped" not in m]
        audit_log({"event": "fsconnect_index_scan", "index_root": self.index_root,
                   "eligible": len(eligible)}, self.config_path)
        return {"op": "index_scan", "index_root": self.index_root,
                "eligible": len(eligible), "files": manifest}

    def _load_cache(self, staging: Path) -> dict:
        """Load the prior run's skip-cache (rel -> {size, mtime, ...}); {} if absent."""
        try:
            data = json.loads((staging / _CACHE_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        files = data.get("files") if isinstance(data, dict) else None
        return files if isinstance(files, dict) else {}

    def _save_cache(self, staging: Path, manifest: list[dict]) -> None:
        """Persist the skip-cache from this run's manifest (atomic tmp + os.replace).

        Only successfully-read/unchanged entries (those carrying a content hash and
        a modtime) are kept; too_large / read_error rows and files that vanished from
        the share are dropped, so the cache self-prunes each run.
        """
        files: dict[str, dict] = {}
        for m in manifest:
            if "sha256" not in m:
                continue
            if "mtime" not in m:
                continue
            files[m["path"]] = {
                "size": m["size"],
                "mtime": m["mtime"],
                "sha256": m["sha256"],
                "injection_flag_count": m.get("injection_flag_count", 0),
            }
        tmp = staging / (_CACHE_NAME + ".tmp")
        tmp.write_text(json.dumps({"files": files}, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, staging / _CACHE_NAME)

    def _cache_probe(self, cache: dict, staging: Path) -> Callable[[str, dict], dict | None]:
        """Return a predicate: cached manifest entry if *rel* is unchanged, else None."""
        def probe(rel: str, entry: dict) -> dict | None:
            prior = cache.get(rel)
            if not prior or prior.get("size") != entry["size"] or prior.get("mtime") != entry["mtime"]:
                return None
            # Size+mtime match, but only skip if the staged copy is actually present
            # -- otherwise a cleared/partial staging dir would silently lose the file.
            if not staging.joinpath(*split_components(rel)).exists():
                return None
            return {"path": rel, "size": entry["size"], "mtime": entry["mtime"],
                    "sha256": prior.get("sha256", ""),
                    "injection_flag_count": prior.get("injection_flag_count", 0),
                    "unchanged": True}
        return probe

    def apply(self, *, staging_dir: str | None = None, reindex: bool = False) -> dict:
        """Stage eligible files into the corpus; optionally trigger a reindex subprocess.

        With ``index_incremental`` set, files whose size+mtime are unchanged since
        the last run (and still present in the staging dir) are skipped -- not
        re-read, re-hashed, or re-written -- a large I/O saving on big shares where
        only a few files change between runs.
        """
        staging = Path(staging_dir) if staging_dir else _DEFAULT_STAGING
        staging.mkdir(parents=True, exist_ok=True)
        incremental = self.fs_cfg.index_incremental
        cache = self._load_cache(staging) if incremental else {}
        copied: list[str] = []
        manifest: list[dict] = []

        def _stage(rel: str, data: bytes) -> None:
            # Re-validate the relative path through the same pathsafe guard the
            # read side uses before joining it into the staging dir. A crafted
            # entry name (path separators, "..", drive letter / ADS) must never
            # escape `staging`; this also makes the join correct on Windows,
            # where `rel` is always "/"-separated.
            dest = staging.joinpath(*split_components(rel))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            copied.append(rel)

        probe = self._cache_probe(cache, staging) if incremental else None
        with ScopedRoots([self.index_root], create=True, allow_unc=self.fs_cfg.allow_unc_roots) as roots:
            # Single pass: _walk reads each CHANGED file once and hands the bytes
            # straight to _stage (bounded memory -- one file held at a time).
            self._walk(roots, "", manifest, on_file=_stage, cached_entry_for=probe)

        # Prune staged copies whose SOURCE file disappeared (codex P1). Staging
        # is a mirror of the share and the retrieval indexer ingests the whole
        # staged tree -- without pruning, a deleted source file stays
        # retrievable indefinitely. This run's manifest is the authoritative
        # current set, so it must be built BEFORE anything is removed.
        pruned = self._prune_staging(staging, {m["path"] for m in manifest})

        unchanged = sum(1 for m in manifest if m.get("unchanged"))
        if incremental:
            self._save_cache(staging, manifest)
        audit_log({"event": "fsconnect_index_apply", "index_root": self.index_root,
                   "staged": len(copied), "unchanged": unchanged, "pruned": len(pruned),
                   "reindex": reindex},
                  self.config_path)
        result = {"op": "index_apply", "index_root": self.index_root,
                  "staged": len(copied), "unchanged": unchanged, "pruned": len(pruned),
                  "staging_dir": str(staging),
                  "reindex_required": True, "reindexed": False}
        if reindex:
            result["reindexed"] = self._run_reindex()
        return result

    def _prune_staging(self, staging: Path, current: set[str]) -> list[str]:
        """Remove staged files absent from *current* (the source manifest).

        The skip-cache file itself is never pruned. Only files are removed --
        empty directories are harmless (the retrieval indexer ingests files,
        not directories) and pruning them would add race surface for no
        retrieval benefit. Returns the pruned "/"-separated rel paths.
        """
        pruned: list[str] = []
        if not staging.is_dir():
            return pruned
        for path in sorted(staging.rglob("*")):
            if not path.is_file() or path.name == _CACHE_NAME:
                continue
            rel = path.relative_to(staging).as_posix()
            if rel not in current:
                path.unlink()
                pruned.append(rel)
        return pruned

    def _run_reindex(self) -> bool:
        """Trigger the existing indexer as a subprocess (decoupled, no import)."""
        try:
            completed = subprocess.run(  # noqa: S603 -- argv list, no shell
                [sys.executable, "-m", "retrieval.indexer"],
                capture_output=True, text=True, timeout=900, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FsConnectRuntimeError("reindex subprocess failed to run",
                                        details={"error": str(exc)}) from exc
        audit_log({"event": "fsconnect_index_reindex", "exit_code": completed.returncode},
                  self.config_path)
        return completed.returncode == 0


__all__ = ["FsIndexer"]
