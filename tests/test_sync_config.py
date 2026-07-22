"""Self-contained tests for sync.config (runnable under `pytest --noconftest`).

No dependence on tests/conftest.py fixtures: uses the builtin tmp_path fixture
and resets the utils.logger config cache between tests so the temp config.yaml
is re-read each time.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from sync.config import RcloneConfig, load_sync_config
from utils.errors import SyncConfigError
from utils.logger import reset_config_cache

# Repo's data/corpus tree -- the only place local_path is allowed to resolve to.
REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_ROOT = (REPO_ROOT / "data" / "corpus").resolve()


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, sync_block: dict) -> str:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "sync": sync_block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def _base_block(**overrides: object) -> dict:
    block = {
        "enabled": True,
        "local_path": "data/corpus",
        "remote_name": "dropbox_cyclaw",
        "remote_path": "CyClaw/corpus",
        "direction": "pull",
    }
    block.update(overrides)
    return block


def test_valid_load(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block())
    cfg = load_sync_config(path)
    assert isinstance(cfg, RcloneConfig)
    # Relative default resolves against repo root into the corpus tree.
    assert cfg.local_path == str(CORPUS_ROOT)
    assert os.path.isabs(cfg.local_path)
    assert cfg.remote == "dropbox_cyclaw:CyClaw/corpus"
    assert cfg.REINDEX_EXIT_CODE == 10
    assert cfg.filter_file is not None and cfg.filter_file.endswith("cyclaw_filters.txt")
    assert cfg.log_path.endswith("rclone_cyclaw.log")


def test_relative_local_path_is_repo_anchored_from_other_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, _base_block())
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.chdir(outside)
    cfg = load_sync_config(path)

    assert cfg.local_path == str(CORPUS_ROOT)


def test_absolute_corpus_subdir_is_accepted(tmp_path: Path) -> None:
    abs_path = str(CORPUS_ROOT / "sub")
    cfg = load_sync_config(_write_config(tmp_path, _base_block(local_path=abs_path)))
    assert cfg.local_path == abs_path


def test_rejects_relative_escape(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(local_path="data/corpus/../../etc"))
    with pytest.raises(SyncConfigError) as exc:
        load_sync_config(path)
    assert exc.value.code == "SYNC_CONFIG_INVALID"


def test_rejects_path_outside_corpus(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(local_path="/tmp/not-corpus"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_repo_data_dir_not_under_corpus(tmp_path: Path) -> None:
    # data/ (parent of corpus) must be rejected -- it is not inside corpus.
    path = _write_config(tmp_path, _base_block(local_path="data"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_bad_direction(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(direction="push"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_remote_name_with_metacharacters(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(remote_name="bad name; rm -rf"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_leading_dash_remote_path(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(remote_path="--config=evil"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_leading_dash_remote_name(tmp_path: Path) -> None:
    # "-foo" matches the ^[A-Za-z0-9_.-]+$ regex (dash is in the class) but would
    # compose into the remote spec "-foo:path" and be parsed by rclone as a flag.
    path = _write_config(tmp_path, _base_block(remote_name="-flag"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_remote_path_with_shell_metachars(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(remote_path="corpus; touch pwned"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_out_of_range_schedule_hour(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(schedule_hour=24))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_out_of_range_schedule_min(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(schedule_min=60))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_rejects_negative_max_delete(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(max_delete=-1))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_sync_timeout_defaults_to_one_hour(tmp_path: Path) -> None:
    # Absent from config -> the hardened default (3600s) bounds a hung rclone.
    cfg = load_sync_config(_write_config(tmp_path, _base_block()))
    assert cfg.sync_timeout_sec == 3600


def test_sync_timeout_override_is_honoured(tmp_path: Path) -> None:
    cfg = load_sync_config(_write_config(tmp_path, _base_block(sync_timeout_sec=120)))
    assert cfg.sync_timeout_sec == 120


def test_sync_timeout_zero_is_allowed_means_unbounded(tmp_path: Path) -> None:
    # 0 is the explicit "disable the timeout" escape hatch (old behaviour).
    cfg = load_sync_config(_write_config(tmp_path, _base_block(sync_timeout_sec=0)))
    assert cfg.sync_timeout_sec == 0


def test_rejects_negative_sync_timeout(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(sync_timeout_sec=-5))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_resilience_and_perf_defaults_are_inert(tmp_path: Path) -> None:
    # Absent from config -> the new knobs default to a no-op (single-shot, no
    # perf flags) so existing deployments behave exactly as before.
    cfg = load_sync_config(_write_config(tmp_path, _base_block()))
    assert cfg.sync_retries == 0
    assert cfg.retry_backoff_sec == 5.0
    assert cfg.fast_list is False
    assert cfg.bwlimit == ""


def test_resilience_and_perf_overrides_honoured(tmp_path: Path) -> None:
    cfg = load_sync_config(
        _write_config(
            tmp_path,
            _base_block(sync_retries=3, retry_backoff_sec=1.5, fast_list=True, bwlimit="8M"),
        )
    )
    assert cfg.sync_retries == 3
    assert cfg.retry_backoff_sec == 1.5
    assert cfg.fast_list is True
    assert cfg.bwlimit == "8M"


def test_rejects_negative_sync_retries(tmp_path: Path) -> None:
    with pytest.raises(SyncConfigError):
        load_sync_config(_write_config(tmp_path, _base_block(sync_retries=-1)))


def test_rejects_negative_retry_backoff(tmp_path: Path) -> None:
    with pytest.raises(SyncConfigError):
        load_sync_config(_write_config(tmp_path, _base_block(retry_backoff_sec=-0.1)))


@pytest.mark.parametrize("value", ["off", "512k", "1.5M", "10G", "1000"])
def test_accepts_valid_bwlimit_forms(tmp_path: Path, value: str) -> None:
    cfg = load_sync_config(_write_config(tmp_path, _base_block(bwlimit=value)))
    assert cfg.bwlimit == value


def test_blank_bwlimit_normalises_to_unset(tmp_path: Path) -> None:
    cfg = load_sync_config(_write_config(tmp_path, _base_block(bwlimit="  ")))
    assert cfg.bwlimit == ""


@pytest.mark.parametrize("value", ["-8M", "8 M", "8M;rm", "fast", "08:00,512k 19:00,off"])
def test_rejects_bad_bwlimit(tmp_path: Path, value: str) -> None:
    # Leading dash (flag injection), embedded whitespace/metachars, bare words and
    # timetables are all refused -- bwlimit must stay a single clean rate token.
    with pytest.raises(SyncConfigError):
        load_sync_config(_write_config(tmp_path, _base_block(bwlimit=value)))


def test_rejects_bad_conflict_resolve(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _base_block(conflict_resolve="random"))
    with pytest.raises(SyncConfigError):
        load_sync_config(path)


def test_missing_block_raises(tmp_path: Path) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(SyncConfigError):
        load_sync_config(str(path))


def test_unknown_keys_fail_closed(tmp_path: Path) -> None:
    # A typo'd key must be FATAL: lenient collection lets a misspelled safety
    # fuse (e.g. "max_delte") silently revert to its default while the operator
    # believes the control is set. "enabled" remains exempt (own toggle).
    path = _write_config(tmp_path, _base_block(typo_field="oops", another="x"))
    with pytest.raises(SyncConfigError) as exc:
        load_sync_config(path)
    assert "typo_field" in str(exc.value) and "another" in str(exc.value)


def test_quoted_bool_master_gate_fails_closed(tmp_path: Path) -> None:
    # bool("false") is True: a quoted YAML string would silently ENABLE the
    # gate it was meant to disable. Safety booleans must be real booleans.
    for key in ("enabled", "post_sync_check", "include_soul", "checksum"):
        sub = tmp_path / key
        sub.mkdir()
        path = _write_config(sub, _base_block(**{key: "false"}))
        with pytest.raises(SyncConfigError, match=rf"sync\.{key} must be a boolean"):
            load_sync_config(path)


def test_include_soul_key_still_loads_as_deprecated_noop(tmp_path: Path) -> None:
    # Config compat: old config.yaml files carry include_soul, and unknown
    # sync: keys are FATAL -- so the key must keep loading. It is now a
    # deprecated no-op: the value is parsed and echoed, but soul data stays
    # un-mirrorable (local_path confined to data/corpus; soul rule
    # unconditional in sync.filters).
    for value in (True, False):
        sub = tmp_path / str(value)
        sub.mkdir()
        cfg = load_sync_config(_write_config(sub, _base_block(include_soul=value)))
        assert cfg.include_soul is value


def test_config_path_identity_carried(tmp_path: Path) -> None:
    # The loader records the path it read -- resolved the SAME way _get_config
    # loads it (repo-root anchored) -- so spawned work (scheduler, auto-reindex)
    # can re-invoke the CLI against byte-identical config identity (codex #592 P1).
    from utils.logger import resolve_config_path

    path = _write_config(tmp_path, _base_block())
    cfg = load_sync_config(path)
    assert cfg._config_path == str(resolve_config_path(path))  # type: ignore[attr-defined]
    assert cfg.repo_root  # canonical root carried for the scheduler


def test_config_path_identity_is_cwd_independent(tmp_path: Path, monkeypatch) -> None:
    # codex #592 P1: the recorded identity must match the file the loader opens.
    # _get_config anchors RELATIVE paths to the repo root, so the recorder must
    # too -- a cwd-anchored os.path.abspath would name a different (or missing)
    # file when the CLI is invoked from any dir but the repo root. Proven at the
    # shared resolver: the same relative path resolves identically from any cwd.
    from utils.logger import resolve_config_path

    default_from_repo = resolve_config_path("config.yaml")
    monkeypatch.chdir(tmp_path)  # cwd != repo root
    assert resolve_config_path("config.yaml") == default_from_repo  # cwd-independent
    assert resolve_config_path("config.yaml") != (tmp_path / "config.yaml")  # not cwd


def test_enabled_flag_read_from_block(tmp_path: Path) -> None:
    # enabled is read out as a plain attribute (not an rclone field). The config
    # cache is process-global, so reset between loads of different temp files.
    d_on = tmp_path / "on"
    d_off = tmp_path / "off"
    d_def = tmp_path / "default"
    for d in (d_on, d_off, d_def):
        d.mkdir()

    on = load_sync_config(_write_config(d_on, _base_block(enabled=True)))
    assert on.enabled is True  # type: ignore[attr-defined]

    reset_config_cache()
    off = load_sync_config(_write_config(d_off, _base_block(enabled=False)))
    assert off.enabled is False  # type: ignore[attr-defined]

    # Absent key defaults to enabled.
    reset_config_cache()
    block = _base_block()
    del block["enabled"]
    default = load_sync_config(_write_config(d_def, block))
    assert default.enabled is True  # type: ignore[attr-defined]


def test_is_windows_property(tmp_path: Path) -> None:
    cfg = load_sync_config(_write_config(tmp_path, _base_block()))
    assert isinstance(cfg.is_windows, bool)


def test_blank_path_overrides_fall_back_to_defaults(tmp_path: Path) -> None:
    # Whitespace-only / empty overrides are treated as "unset" and replaced by
    # the computed defaults, never passed verbatim to rclone (which would fail
    # with a cryptic "file not found: ''").
    cfg = load_sync_config(
        _write_config(tmp_path, _base_block(filter_file="  ", workdir=" ", log_dir=""))
    )
    assert cfg.filter_file.strip() and cfg.filter_file.endswith("cyclaw_filters.txt")
    assert cfg.workdir.strip() and cfg.workdir.endswith("bisync_state")
    assert cfg.log_dir.strip() and cfg.log_dir.endswith("logs")
    # log_path derives from the (now guaranteed non-empty) log_dir.
    assert cfg.log_path.endswith("rclone_cyclaw.log")


def test_path_override_expanding_to_empty_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-blank override that expands (via env var) to an empty string must
    # fail fast with a clear SyncConfigError rather than reach rclone as "".
    monkeypatch.setenv("CYCLAW_EMPTY_PATH_TEST", "")
    with pytest.raises(SyncConfigError):
        load_sync_config(
            _write_config(tmp_path, _base_block(filter_file="$CYCLAW_EMPTY_PATH_TEST"))
        )
