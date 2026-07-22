"""Self-contained tests for sync.filters (runnable under `pytest --noconftest`).

No dependence on tests/conftest.py fixtures: uses tmp_path and resets the
utils.logger config cache between tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sync.config import load_sync_config
from sync.filters import filter_summary, generate_filters, write_filter_file
from utils.logger import reset_config_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUL_RULE = "- data/personality/**"


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _load(tmp_path: Path, **overrides: object):
    block = {
        "enabled": True,
        "local_path": "data/corpus",
        "remote_name": "dropbox_cyclaw",
        "remote_path": "CyClaw/corpus",
        "direction": "pull",
    }
    block.update(overrides)
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "sync": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return load_sync_config(str(path))


def test_soul_excluded_by_default(tmp_path: Path) -> None:
    text = generate_filters(_load(tmp_path))
    assert SOUL_RULE in text
    assert "WARNING" not in text


def test_hardened_categories_present(tmp_path: Path) -> None:
    text = generate_filters(_load(tmp_path))
    for rule in (
        "- *.gguf",
        "- index/**",
        "- .emb_cache/**",
        "- .chroma/**",
        "- logs/**",
        "- *.jsonl",
        "- *.db",
        "- *.db-wal",
        "- *.db-shm",
        "- .env",
        "- credentials*",
        "- .git/**",
        "- .gitignore",
        "- desktop.ini",
        "- .rclone-state/**",
    ):
        assert rule in text, f"missing hardened rule: {rule}"


def test_soul_excluded_even_when_include_soul(tmp_path: Path) -> None:
    # include_soul is a deprecated no-op (kept for config compat): the soul
    # rule is unconditional and must survive include_soul=True with no
    # "is being synced" warning header.
    text = generate_filters(_load(tmp_path, include_soul=True))
    assert SOUL_RULE in text
    assert "WARNING" not in text
    # Other hardened rules survive.
    assert "- *.db" in text


def test_extra_excludes_appended_after_hardened_block(tmp_path: Path) -> None:
    text = generate_filters(_load(tmp_path, extra_excludes=["scratch/**", "- already/**"]))
    lines = text.splitlines()
    # Bare entry gets a leading "- "; entry with leading "- " is preserved.
    assert "- scratch/**" in lines
    assert "- already/**" in lines
    # Extras come after the hardened soul rule.
    assert lines.index("- scratch/**") > lines.index(SOUL_RULE)
    assert lines.index("- scratch/**") > lines.index("- *.gguf")


def test_bang_extra_exclude_is_neutralised(tmp_path: Path) -> None:
    # rclone's '!' rule clears the whole filter list built so far. A user extra
    # must never be able to wipe the hardened soul/secret/index exclusions, so a
    # '!' entry is dropped (as a comment) rather than emitted as a live rule.
    text = generate_filters(_load(tmp_path, extra_excludes=["!", "! reset"]))
    lines = text.splitlines()
    # No live (non-comment) line is a bare '!' reset.
    assert "!" not in [ln for ln in lines if not ln.startswith("#")]
    assert not any(ln.strip() == "!" for ln in lines if not ln.startswith("#"))
    # The hardened soul rule still precedes any extras and survives intact.
    assert SOUL_RULE in text
    assert any("IGNORED" in ln and "!" in ln for ln in lines)


def test_multiline_extra_exclude_cannot_smuggle_bang_reset(tmp_path: Path) -> None:
    # A single extra_excludes entry that embeds a newline must not be able to
    # smuggle a live '!' reset past the guard. The entry below strips to a value
    # that does NOT start with '!', so a whole-value check would prepend '- ' and
    # emit a bare '!' line that wipes every hardened exclusion above it.
    text = generate_filters(_load(tmp_path, extra_excludes=["scratch/**\n!"]))
    lines = text.splitlines()
    live = [ln for ln in lines if not ln.startswith("#")]
    # No live line is a bare '!' reset...
    assert not any(ln.strip() == "!" for ln in live)
    # ...the benign first physical line is still applied as an exclude...
    assert "- scratch/**" in lines
    # ...the smuggled '!' is neutralised with a loud comment...
    assert any("IGNORED" in ln and "!" in ln for ln in lines)
    # ...and the hardened soul/secret exclusions survive intact.
    assert SOUL_RULE in text
    assert "- *.db" in text


def test_multiline_extra_exclude_each_line_validated(tmp_path: Path) -> None:
    # Every physical line of a multi-line entry is validated independently: bare
    # patterns get '- ' prepended, blanks are dropped, and explicit '- '/'+ '
    # prefixes are preserved -- matching the single-entry behaviour.
    text = generate_filters(_load(tmp_path, extra_excludes=["a/**\n\n- b/**\n+ c/**"]))
    lines = text.splitlines()
    assert "- a/**" in lines
    assert "- b/**" in lines
    assert "+ c/**" in lines


def test_crlf_extra_exclude_split_too(tmp_path: Path) -> None:
    # splitlines() handles CRLF, so a Windows-style multi-line entry is split the
    # same way and a CR-prefixed '!' cannot survive as a live reset rule.
    text = generate_filters(_load(tmp_path, extra_excludes=["keep/**\r\n!"]))
    live = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert not any(ln.strip() == "!" for ln in live)
    assert "- keep/**" in text


def test_write_filter_file_is_atomic_no_tmp_left(tmp_path: Path) -> None:
    target = tmp_path / "state" / "cyclaw_filters.txt"
    cfg = _load(tmp_path, filter_file=str(target))
    write_filter_file(cfg)
    # The temp file used for the atomic replace must not linger.
    assert not (target.parent / f"{target.name}.tmp").exists()
    assert SOUL_RULE in target.read_text(encoding="utf-8")


def test_write_filter_file_returns_abs_path(tmp_path: Path) -> None:
    target = tmp_path / "state" / "cyclaw_filters.txt"
    cfg = _load(tmp_path, filter_file=str(target))
    written = write_filter_file(cfg)
    assert written == str(target.resolve())
    assert Path(written).is_file()
    assert SOUL_RULE in Path(written).read_text(encoding="utf-8")


def test_filter_summary_shape(tmp_path: Path) -> None:
    cfg = _load(tmp_path, extra_excludes=["scratch/**"])
    summary = filter_summary(cfg)
    assert set(summary) == {"soul_excluded", "include_soul", "total_rules", "filter_file", "extra_excludes"}
    assert summary["soul_excluded"] is True
    assert summary["include_soul"] is False
    assert isinstance(summary["total_rules"], int) and summary["total_rules"] > 0
    assert summary["extra_excludes"] == ["scratch/**"]


def test_filter_summary_soul_excluded_even_when_include_soul(tmp_path: Path) -> None:
    # The soul exclusion is unconditional; include_soul only echoes the raw
    # (deprecated, no-op) configured value for audit.
    summary = filter_summary(_load(tmp_path, include_soul=True))
    assert summary["soul_excluded"] is True
    assert summary["include_soul"] is True
