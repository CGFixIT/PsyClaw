"""Enable CyClaw's confined macOS read/list profile in the active config file."""

from __future__ import annotations

import argparse
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

LOGGER = logging.getLogger(__name__)
_READ_OPS = ["fs_list", "fs_stat", "fs_read"]
_SAFETY_VALUES: dict[str, object] = {
    "enabled": True,
    "allowed_fs_ops": _READ_OPS,
    "writes_enabled": False,
    "strict_roots": True,
    "index_enabled": False,
    "allow_hard_delete": False,
    "allow_unc_roots": False,
    "allow_macos_volume_roots": False,
    "follow_symlinks": False,
    "scan_content": True,
}


def _load_document(text: str) -> dict[str, Any]:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("config must contain a top-level YAML mapping")
    block = loaded.get("fsconnect")
    if not isinstance(block, dict):
        raise ValueError("config must contain an existing fsconnect mapping")
    return loaded


def _replace_fsconnect_block(source: str, block: dict[str, Any]) -> str:
    lines = source.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.startswith("fsconnect:")),
        None,
    )
    if start is None:
        raise ValueError("could not locate the top-level fsconnect block")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line[0].isspace():
            end = index
            break

    rendered = yaml.safe_dump(
        {"fsconnect": block},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    ).rstrip()
    suffix = "".join(lines[end:])
    separator = "\n\n" if suffix else "\n"
    return "".join(lines[:start]) + rendered + separator + suffix


def _assert_contract(document: dict[str, Any], root: str) -> None:
    block = document["fsconnect"]
    expected = {
        **_SAFETY_VALUES,
        "allowed_roots": [root],
        "writable_roots": [root],
    }
    mismatched = {key: block.get(key) for key, value in expected.items() if block.get(key) != value}
    if mismatched:
        raise ValueError(f"rendered fsconnect contract mismatch: {sorted(mismatched)}")


def enable_readlist(config_path: Path, root_path: Path) -> bool:
    """Apply the fail-closed macOS read/list profile; return whether bytes changed."""
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError(f"config must be a regular, non-symlink file: {config_path}")
    if root_path.is_symlink() or not root_path.is_dir():
        raise ValueError(f"fsconnect root must be a regular directory: {root_path}")

    resolved_root = str(root_path.resolve(strict=True))
    source = config_path.read_text(encoding="utf-8")
    document = _load_document(source)
    block = dict(document["fsconnect"])
    block.update(_SAFETY_VALUES)
    block["allowed_roots"] = [resolved_root]
    block["writable_roots"] = [resolved_root]

    rendered = _replace_fsconnect_block(source, block)
    _assert_contract(_load_document(rendered), resolved_root)
    if rendered == source:
        return False

    original_mode = stat.S_IMODE(config_path.stat().st_mode)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path = Path(temp_name)
        temp_path.chmod(original_mode)
        os.replace(temp_path, config_path)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[cyclaw] %(message)s")
    args = build_parser().parse_args()
    try:
        changed = enable_readlist(args.config.expanduser(), args.root.expanduser())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        LOGGER.error("fsconnect setup failed: %s", exc)
        return 1
    LOGGER.info("fsconnect config %s", "updated" if changed else "already safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
