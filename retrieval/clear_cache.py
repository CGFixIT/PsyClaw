"""Command-line entry point: clear CyClaw's local embedding cache.

The embedding cache is the on-disk directory where ``sentence-transformers``
materializes the downloaded model weights -- ``models.embeddings.cache_dir`` in
``config.yaml`` (default ``.emb_cache``). It is a regenerable runtime artifact:
deleting it only forces the next run to re-download / re-load the model. Nothing
in the RAG index, audit log, or soul state is touched.

Usage::

    python -m retrieval.clear_cache              # preview only (safe default)
    python -m retrieval.clear_cache --apply      # actually delete the cache dir
    python -m retrieval.clear_cache --config path/to/config.yaml

Per the CyClaw project rule for data-modifying scripts, the default is a safe
dry-run preview; ``--apply`` is required to actually remove anything.

This module never imports ``gate.py``, ``graph.py``, or ``mcp_hybrid_server.py``.

Exit codes::

    0   success (cache cleared, already absent, or dry-run preview)
    2   deletion failed (filesystem error)
    3   config / environment problem (config unreadable or cache_dir unset)
"""

from __future__ import annotations

# This process never imports gate.py, so without this it would inherit
# whatever telemetry env the operator's shell/container/observability agent
# happens to carry. resolve_cache_dir (the one retrieval.embeddings symbol
# this file uses) pulls in no telemetry-emitting library today, so this is
# prophylactic -- but every other CyClaw entry point applies the same block
# unconditionally rather than betting on what a future edit here imports.
from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

import argparse  # noqa: E402 - must follow the telemetry kill above
import shutil  # noqa: E402 - must follow the telemetry kill above
import sys  # noqa: E402 - must follow the telemetry kill above
from pathlib import Path  # noqa: E402 - must follow the telemetry kill above

import yaml  # noqa: E402 - must follow the telemetry kill above

from retrieval.embeddings import resolve_cache_dir  # noqa: E402 - must follow the telemetry kill above
from utils.errors import ConfigError  # noqa: E402 - must follow the telemetry kill above

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3


def read_cache_dir(config_path: str) -> str:
    """Return ``models.embeddings.cache_dir`` from config, or "" if unset.

    Raises ``ConfigError`` when the file cannot be read/parsed or the
    ``models.embeddings`` section is missing -- callers map this to exit 3.
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except OSError as exc:
        raise ConfigError(f"Could not read config '{config_path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in '{config_path}': {exc}") from exc
    try:
        cache_dir = cfg["models"]["embeddings"].get("cache_dir", "")
    except (KeyError, TypeError) as exc:
        raise ConfigError(
            f"config '{config_path}' missing models.embeddings section"
        ) from exc
    return resolve_cache_dir(config_path, cache_dir)


def dir_stats(path: Path) -> tuple[int, int]:
    """Return ``(file_count, total_bytes)`` for an existing directory tree."""
    files = 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            try:
                total += p.stat().st_size
            except OSError:
                # A file vanished mid-walk; ignore it for the size estimate.
                pass
    return files, total


def human_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string (1 KiB = 1024 B)."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _ok(text: str) -> None:
    print(f"  [OK  ] {text}")


def _info(text: str) -> None:
    print(f"  [INFO] {text}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def clear_cache(config_path: str, apply: bool) -> int:
    """Clear the configured embedding cache directory.

    With ``apply=False`` (default) this only previews what would be removed.
    Returns a process exit code.
    """
    print(f"\nCyClaw -- Clear embedding cache{'' if apply else ' (dry-run)'}")
    print("-" * 40)

    try:
        cache_dir = read_cache_dir(config_path)
    except ConfigError as exc:
        _err(exc.message)
        return EXIT_ENV

    if not cache_dir:
        _info(
            "models.embeddings.cache_dir is unset; sentence-transformers uses its "
            "default cache outside the project. Nothing to clear here."
        )
        return EXIT_OK

    path = Path(cache_dir)
    _info(f"cache_dir: {path}")

    if not path.exists():
        _ok("Cache directory does not exist; nothing to clear.")
        return EXIT_OK

    if not path.is_dir():
        _err(f"cache_dir '{path}' exists but is not a directory; refusing to remove.")
        return EXIT_FAIL

    files, total = dir_stats(path)
    _info(f"contents: {files} file(s), {human_size(total)}")

    if not apply:
        _info("Dry-run: nothing deleted. Re-run with --apply to remove the cache.")
        return EXIT_OK

    try:
        shutil.rmtree(path)
    except OSError as exc:
        _err(f"Failed to delete '{path}': {exc}")
        return EXIT_FAIL

    _ok(f"Removed embedding cache: {path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m retrieval.clear_cache",
        description="Clear CyClaw's local embedding cache (regenerable artifact).",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to CyClaw config.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete the cache directory (default is a safe dry-run preview).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return clear_cache(args.config, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
