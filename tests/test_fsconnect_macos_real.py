"""Real, unmocked execution tests for agentic.fsconnect.pathsafe's macOS-only
policy helpers.

test_fsconnect_macos_policy.py proves these functions' LOGIC by forcing
``sys.platform == "darwin"`` (via monkeypatch) and faking ``os.stat`` results
regardless of the host OS running the suite -- that is deliberate and valuable
for host-independent coverage, but it never actually executes the Darwin
branch on a real Darwin kernel with real filesystem state.

This file is the opposite: every test here either (a) skips cleanly unless
the host really is Darwin (``sys.platform == "darwin"`` unmodified -- no
monkeypatching of ``sys.platform``, ``os.stat``, or ``os.close`` anywhere in
this module), and then exercises the real function against real files/dirs
created under ``tmp_path``, or (b) for the one genuinely platform-independent
property (case-insensitive filesystem identity), uses a runtime filesystem
probe instead of guessing from ``sys.platform`` -- the same technique
``_is_real_descendant``'s own docstring explains and that
``test_fsconnect_pathsafe.py``'s ``_tmp_is_case_sensitive`` helper already
uses elsewhere in this suite.

On this Linux sandbox (and any non-Darwin CI runner) the Darwin-only tests
below skip rather than run a simulation -- there is nothing to fake here, by
design. They only produce real signal on an actual Mac.

No SF_DATALESS / iCloud-dataless coverage lives here on purpose. A genuine
dataless placeholder requires live iCloud Drive sync state -- a file that has
actually been evicted to the cloud by the OS after real account sign-in and
real sync activity -- which cannot be created deterministically, or often at
all, on a fresh CI runner with no iCloud account configured. This file does
not attempt to fake that condition for real (setting ``st_flags`` by hand on
a real file is not the same claim as a real eviction). ``_is_macos_dataless``
therefore has exactly one source of truth in this repository:
``test_fsconnect_macos_policy.py``'s
``test_dataless_flag_is_authoritative_regardless_of_logical_size`` -- and that
coverage should be read as simulated-only, never as proof against a real
iCloud-evicted file.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from agentic.fsconnect import pathsafe
from utils.errors import FsMacOSPermissionError, FsPathError


@pytest.mark.skipif(sys.platform != "darwin", reason="/Volumes is a real macOS system path")
def test_volumes_gate_refuses_by_default_and_allows_when_opted_in(tmp_path: Path) -> None:
    """Real ``/Volumes`` gate, on a real Mac, with no faked ``os.stat``.

    This only proves the gate itself (refuse by default, allow when
    ``allow_macos_volume_roots=True``) against the always-present ``/Volumes``
    directory. It does NOT prove behavior against an actually-mounted
    external/removable volume under it -- there is no way to attach one
    deterministically in CI, so that scenario is out of scope here.
    """
    volumes_resolved = str(Path("/Volumes").resolve())
    assert pathsafe._is_macos_volume_path(volumes_resolved) is True
    assert pathsafe._is_macos_volume_path(str(tmp_path.resolve())) is False

    with pytest.raises(FsPathError, match="allow_macos_volume_roots is false"):
        pathsafe.ScopedRoots(["/Volumes"], create=False)

    with pathsafe.ScopedRoots(["/Volumes"], create=False, allow_macos_volume_roots=True) as roots:
        assert len(roots.roots) == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple metadata/dataless policy is Darwin-only by design")
def test_apple_metadata_and_dataless_names_are_filtered_for_real(tmp_path: Path) -> None:
    """Real files on a real Darwin filesystem, real ``sys.platform == "darwin"``.

    No monkeypatching is needed: this test body only ever runs when the host
    genuinely is Darwin, so ``_is_macos_artifact_name``'s
    ``sys.platform == "darwin"`` branch executes for real. Also exercises
    ``_filter_macos_entries`` end to end over a real directory with a real
    ``os.stat``-based ``stat_entry`` callable, not just the name-matching
    function in isolation.
    """
    files = {
        ".DS_Store": "ds_store junk",
        ".localized": "",
        "._foo": "apple double resource-fork junk",
        ".envrc": "export FOO=bar",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    assert pathsafe._is_macos_artifact_name(".DS_Store") is True
    assert pathsafe._is_macos_artifact_name(".localized") is True
    assert pathsafe._is_macos_artifact_name("._foo") is True
    assert pathsafe._is_macos_artifact_name(".envrc") is False

    def stat_entry(name: str) -> os.stat_result:
        return os.stat(tmp_path / name)

    visible = pathsafe._filter_macos_entries(list(files), stat_entry)
    assert [name for name, _st in visible] == [".envrc"]


def _tmp_is_case_sensitive(tmp_path: Path) -> bool:
    """Runtime probe, not a platform guess -- same technique as CPython's own
    Lib/test/support/os_helper.fs_is_case_insensitive (see pathsafe's
    _is_real_descendant docstring for why guessing by platform is wrong), and
    the exact pattern already used by test_fsconnect_pathsafe.py's copy of
    this helper. Duplicated here rather than imported: small test-only probe
    helpers are expected to be copied per file in this suite, not shared.
    """
    probe = tmp_path / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return not (tmp_path / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def test_case_insensitive_root_overlap_detected_for_real(tmp_path: Path) -> None:
    """Real-filesystem regression test for the case-insensitive-APFS
    root-overlap bug class fixed by PRs #886/#887/#888 earlier in this
    project's history.

    No hardcoded platform skip: case-insensitive-but-case-preserving
    behavior is the real macOS APFS default, but it is a per-volume,
    format-time choice, not something guessable from ``sys.platform`` (see
    ``_is_real_descendant``'s docstring) -- and it is NOT what a typical
    Linux ext4 tmp filesystem does. So this asks the real filesystem via the
    ``_tmp_is_case_sensitive`` probe and skips cleanly when the answer is
    "case-sensitive" (e.g. this Linux sandbox), since the scenario under
    test literally cannot occur there: two differently-cased spellings of a
    case-sensitive path name two different, non-overlapping entities.
    """
    if _tmp_is_case_sensitive(tmp_path):
        pytest.skip("tmp_path filesystem is case-sensitive; case-insensitive root overlap does not apply here")

    vault = tmp_path / "Vault"
    vault.mkdir()
    vault_lower = tmp_path / "vault"

    # Both spellings must resolve to the same real, on-disk entity -- proven
    # by filesystem identity (st_dev/st_ino via os.path.samestat), not by
    # comparing the path strings.
    assert pathsafe._is_same_entity(str(vault), str(vault_lower)) is True

    with pytest.raises(FsPathError, match="overlapping roots"):
        pathsafe.ScopedRoots([str(vault), str(vault_lower)], create=False)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="_raise_macos_permission only maps errors to FsMacOSPermissionError on Darwin by design",
)
def test_real_eacces_maps_to_typed_permission_error(tmp_path: Path) -> None:
    """Closest CI can get to a real TCC-style denial without a scriptable
    macOS privacy prompt: this triggers a genuine EACCES from the kernel by
    chmod-ing a real directory to 0o000, not a mocked/simulated one.
    """
    if os.geteuid() == 0:
        pytest.skip("running as root bypasses POSIX permission bits; a real EACCES cannot be produced")

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(OSError) as os_exc:
            os.listdir(locked)
        assert os_exc.value.errno in {errno.EACCES, errno.EPERM}

        with pytest.raises(FsMacOSPermissionError) as caught:
            pathsafe._raise_macos_permission("listing a locked directory", os_exc.value)
        assert caught.value.code == "FSCONNECT_MACOS_PERMISSION_DENIED"
    finally:
        # Restore permissions before tmp_path's own fixture teardown runs,
        # so cleanup doesn't itself hit the wall we just built.
        os.chmod(locked, 0o700)
