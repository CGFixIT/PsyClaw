"""Operator-facing pre-flight self-test for ``python -m sync.cli test``.

This is NOT the pytest suite. It is a fast, no-mocking smoke test runnable on
any machine to confirm the sync module will work in this environment. It
exercises the config loader, filter writer, version check, and the argv
builders, but it does NOT actually contact Dropbox. The dry-run reachability
check is best-effort and tolerates a missing rclone binary by reporting a
failed point rather than crashing.

Use the pytest suite (``tests/test_sync_runner.py`` / ``tests/test_sync_cli.py``)
for mocked unit coverage.
"""

from __future__ import annotations

import os

from sync.config import RcloneConfig, load_sync_config
from sync.filters import filter_summary, generate_filters, write_filter_file
from sync.runner import build_bisync_argv, build_pull_argv, check_rclone_version
from utils.errors import RcloneNotInstalledError, RcloneTimeoutError, RcloneVersionError, SyncConfigError
from utils.selftest import fail, finalize, ok, skip


def run_self_test(
    config_path: str = "config.yaml",
    dry_run: bool = True,
) -> tuple[int, int, list[str]]:
    """Run all pre-flight checks. Returns ``(passed, total, output_lines)``."""
    results: list[tuple[bool, str]] = []
    cfg: RcloneConfig

    # 1. config.yaml exists, parses, and validates.
    try:
        cfg = load_sync_config(config_path)
        results.append(ok("01. Config loads and validates"))
    except SyncConfigError as exc:
        results.append(fail("01. Config loads and validates", exc.message))
        # Cannot continue without a config; the rest are skipped.
        for n in range(2, 9):
            results.append(skip(f"{n:02d}. (skipped -- no config)", "config invalid"))
        return finalize(results)

    # 2. local_path is absolute (and ideally an existing directory).
    if os.path.isabs(cfg.local_path):
        if os.path.isdir(cfg.local_path):
            results.append(ok("02. local_path is an existing directory"))
        else:
            results.append(skip(
                "02. local_path is an existing directory",
                f"path does not yet exist: {cfg.local_path}",
            ))
    else:
        results.append(fail("02. local_path is absolute", f"got relative: {cfg.local_path}"))

    # 3. rclone installed and recent enough (floor 1.68.2). Tolerate absence.
    try:
        v = check_rclone_version()
        results.append(ok(f"03. rclone {v[0]}.{v[1]}.{v[2]} installed (>= 1.68.2)"))
    except (RcloneNotInstalledError, RcloneTimeoutError, RcloneVersionError) as exc:
        results.append(fail("03. rclone >= 1.68.2 installed", exc.message))

    # 4. Filter content asserts the hardened soul exclusion -- unconditional;
    #    the deprecated include_soul key can no longer drop it.
    text = generate_filters(cfg)
    if "- data/personality/**" in text:
        results.append(ok("04. data/personality/** excluded (unconditional)"))
    else:
        results.append(fail(
            "04. data/personality/** excluded (unconditional)",
            "soul exclusion missing from filter",
        ))

    # 5. Filter file can be written to disk.
    try:
        path = write_filter_file(cfg)
        results.append(ok(f"05. Wrote filter file: {path}"))
    except OSError as exc:
        results.append(fail("05. Write filter file", str(exc)))

    # 6. Filter summary is consistent with the (unconditional) soul exclusion.
    summary = filter_summary(cfg)
    if summary["soul_excluded"] is True:
        results.append(ok("06. Filter summary consistent with config"))
    else:
        results.append(fail("06. Filter summary consistent with config", f"got: {summary}"))

    # 7. Pull argv is well-formed (subprocess is NOT invoked here).
    try:
        argv = build_pull_argv(cfg, dry_run=dry_run, log_path=cfg.log_path)
        if isinstance(argv, list) and argv[1] == "copy" and cfg.remote in argv and cfg.local_path in argv:
            results.append(ok("07. Pull argv well-formed (list, no shell)"))
        else:
            results.append(fail("07. Pull argv well-formed", f"unexpected argv: {argv[:6]}"))
    except (OSError, ValueError) as exc:
        results.append(fail("07. Pull argv well-formed", str(exc)))

    # 8. Bisync argv is well-formed.
    try:
        argv = build_bisync_argv(cfg, dry_run=dry_run, log_path=cfg.log_path)
        if isinstance(argv, list) and argv[1] == "bisync" and cfg.remote in argv and cfg.local_path in argv:
            results.append(ok("08. Bisync argv well-formed (list, no shell)"))
        else:
            results.append(fail("08. Bisync argv well-formed", f"unexpected argv: {argv[:6]}"))
    except (OSError, ValueError) as exc:
        results.append(fail("08. Bisync argv well-formed", str(exc)))

    return finalize(results)


if __name__ == "__main__":
    p, t, out = run_self_test()
    for ln in out:
        print(ln)
    print(f"\n{p}/{t} passed")
    raise SystemExit(0 if p == t else 1)
