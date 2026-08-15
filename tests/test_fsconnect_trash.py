"""Unit tests for agentic.fsconnect.trash helpers (Phase 2 item 4).

Pure policy/formatting: entry-name sortability + collision-proofing, meta sidecar
round-trip, retention/expiry logic (unparseable expiry treated as expired), and the
find_entry lookup contract. No filesystem needed for the helper math.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agentic.fsconnect import trash
from utils.errors import FsConnectRuntimeError

NOW = datetime(2026, 7, 9, 14, 55, 1, tzinfo=UTC)


def test_entry_name_is_sortable_and_hashed():
    name = trash.entry_name("sub/dir/file.txt", NOW)
    assert name.startswith("20260709T145501Z__")
    assert "sub__dir__file.txt" in name
    # 8-hex path digest + 8-hex random suffix
    assert len(name.rsplit("__", 1)[-1]) == 16


def test_entry_name_disambiguates_same_second():
    a = trash.entry_name("a.txt", NOW)
    b = trash.entry_name("b.txt", NOW)
    assert a != b  # different original paths -> different digest


def test_entry_name_no_collision_same_path_same_second():
    # The digest's only inputs (path + second-resolution stamp) are identical
    # here, so the random suffix must carry the disambiguation.
    a = trash.entry_name("a.txt", NOW)
    b = trash.entry_name("a.txt", NOW)
    assert a != b


def test_meta_roundtrip():
    entry = trash.make_entry(
        "notes/x.txt", NOW, reason="cleanup", sha256="abc", size=42,
        kind="file", retention_days=30)
    data = entry.meta_bytes()
    parsed = trash._parse_meta(f"{entry.name}.meta.json", data)
    assert parsed is not None
    assert parsed.original_path == "notes/x.txt"
    assert parsed.reason == "cleanup"
    assert parsed.sha256 == "abc"
    assert parsed.size == 42
    assert parsed.kind == "file"


def test_retention_expiry_computation():
    entry = trash.make_entry(
        "x.txt", NOW, reason="r", sha256=None, size=0, kind="file",
        retention_days=30)
    expected = trash.iso(NOW + timedelta(days=30))
    assert entry.retention_expires_at == expected


def test_expired_selects_past_entries():
    fresh = trash.make_entry("fresh.txt", NOW, reason="r", sha256=None,
                             size=0, kind="file", retention_days=30)
    old = trash.make_entry("old.txt", NOW - timedelta(days=40), reason="r",
                           sha256=None, size=0, kind="file", retention_days=30)
    got = trash.expired([fresh, old], NOW)
    names = {e.name for e in got}
    assert old.name in names
    assert fresh.name not in names


def test_expired_treats_unparseable_as_expired():
    bad = trash.TrashEntry(
        name="bad", original_path="p", deleted_at="", reason="", sha256=None,
        size=0, kind="file", retention_expires_at="not-a-date")
    assert trash.expired([bad], NOW) == [bad]


def test_parse_meta_rejects_garbage():
    assert trash._parse_meta("x.meta.json", b"not json") is None
    assert trash._parse_meta("x.meta.json", b"[]") is None  # not a dict


def test_parse_meta_filename_beats_a_diverging_name_field():
    """The sidecar's filename is authoritative; its "name" field is advisory.

    The blob is ``<name>`` and the sidecar is ``<name>.meta.json``, so the
    filename is the only value the filesystem enforces. Trusting the contents
    instead let a divergent sidecar (crash-truncated write, hand edit, a
    restore-then-repurge race) name an entry that is not on disk, so trash_empty
    unlinked a path that did not exist and the real blob stayed forever.
    """
    entry = trash.make_entry(
        "notes/x.txt", NOW, reason="cleanup", sha256=None, size=1,
        kind="file", retention_days=30)
    raw = json.loads(entry.meta_bytes())
    raw["name"] = "ghost"  # diverge the field from the filename
    parsed = trash._parse_meta(f"{entry.name}{trash.META_SUFFIX}", json.dumps(raw).encode())
    assert parsed is not None
    assert parsed.name == entry.name, "sidecar contents must not override the on-disk filename"
    assert parsed.name != "ghost"


def test_parse_meta_survives_a_sidecar_with_no_name_field():
    """A sidecar predating/lacking "name" still resolves from its filename."""
    entry = trash.make_entry(
        "notes/y.txt", NOW, reason="cleanup", sha256=None, size=1,
        kind="file", retention_days=30)
    raw = json.loads(entry.meta_bytes())
    raw.pop("name", None)
    parsed = trash._parse_meta(f"{entry.name}{trash.META_SUFFIX}", json.dumps(raw).encode())
    assert parsed is not None
    assert parsed.name == entry.name


def test_find_entry_missing_raises():
    with pytest.raises(FsConnectRuntimeError):
        trash.find_entry([], "nope")
