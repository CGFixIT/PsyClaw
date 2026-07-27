"""Unit tests for utils/logger.py — audit logging + logging setup.

Focuses on the cwd-independence of relative logging.log_file / audit_file
config values (_anchor / _REPO_ROOT). _get_config already anchored the
config.yaml *file itself* to _REPO_ROOT; these guard the values *inside* it,
which previously stayed cwd-relative -- silent until CyClaw is launched from
a cwd other than the repo root (the same fragility gate.py's _BASE_DIR exists
to prevent for config.yaml/static/).
"""

import json

import pytest

from utils import logger


@pytest.fixture(autouse=True)
def _isolate_logger_state():
    # audit_log() caches one append-mode file handle per resolved path in a
    # module-level dict; close it around each test so a test's tmp_path file
    # can be deleted and the next test starts with a clean cache.
    logger.close_audit_handles()
    logger.reset_config_cache()
    yield
    logger.close_audit_handles()
    logger.reset_config_cache()


class TestAnchor:
    def test_relative_path_anchored_to_repo_root(self):
        assert logger._anchor("logs/audit.jsonl") == logger._REPO_ROOT / "logs/audit.jsonl"

    def test_absolute_path_passed_through(self, tmp_path):
        absolute = tmp_path / "audit.jsonl"
        assert logger._anchor(str(absolute)) == absolute

    def test_user_expansion(self, monkeypatch, tmp_path):
        # posixpath.expanduser reads HOME; Windows' ntpath.expanduser reads
        # USERPROFILE (falling back to HOMEDRIVE+HOMEPATH) and never reads HOME
        # at all (verified against CPython's ntpath.py) -- set both so this
        # passes on every CI leg instead of silently no-op'ing on windows-latest.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert logger._anchor("~/audit.jsonl") == tmp_path / "audit.jsonl"


class TestAuditLogPathAnchoring:
    def test_relative_audit_file_resolves_regardless_of_cwd(self, tmp_path, monkeypatch):
        # Regression: audit_log() previously did Path(cfg["logging"]["audit_file"])
        # directly, resolving a relative path against the process cwd instead
        # of the repo root.
        monkeypatch.setattr(logger, "_REPO_ROOT", tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        cfg = {"logging": {"audit_file": "relative_audit.jsonl", "audit_fields": {}}}
        logger.audit_log({"event": "test_event"}, cfg=cfg)
        logger.close_audit_handles()

        expected = tmp_path / "relative_audit.jsonl"
        assert expected.exists()
        assert not (elsewhere / "relative_audit.jsonl").exists()
        record = json.loads(expected.read_text().splitlines()[0])
        assert record["event"] == "test_event"

    def test_absolute_audit_file_unaffected(self, tmp_path):
        absolute = tmp_path / "abs_audit.jsonl"
        cfg = {"logging": {"audit_file": str(absolute), "audit_fields": {}}}
        logger.audit_log({"event": "test_event"}, cfg=cfg)
        logger.close_audit_handles()
        assert absolute.exists()


class TestAuditLogWriteFailure:
    """audit_logger is the unconditional terminal node every graph path
    converges on (invariant I4), running AFTER the answer is already
    computed. A disk/permission failure writing the audit line must degrade
    to a warning, not raise -- an already-good response should never become
    an HTTP 500 purely because the audit trail couldn't be persisted."""

    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch, caplog):
        cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}

        def _boom(_log_path):
            raise OSError("simulated disk full")

        monkeypatch.setattr(logger, "_audit_handle", _boom)
        with caplog.at_level("WARNING", logger="cyclaw.logger"):
            logger.audit_log({"event": "test_event"}, cfg=cfg)  # must not raise
        assert "audit_log write failed" in caplog.text

    def test_write_failure_leaves_no_partial_file_state(self, tmp_path, monkeypatch):
        # A failed handle open must not leave the caller's dict mutated or
        # raise something other than the documented fail-soft path.
        cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}
        event = {"event": "test_event"}
        monkeypatch.setattr(logger, "_audit_handle", lambda _log_path: (_ for _ in ()).throw(OSError("boom")))
        logger.audit_log(event, cfg=cfg)
        assert event == {"event": "test_event"}  # caller's dict is never mutated


class TestSetupLoggingPathAnchoring:
    def test_relative_log_file_resolves_regardless_of_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setattr(logger, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(logger, "_logging_initialized", False)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        cfg = {"logging": {"level": "INFO", "log_file": "relative.log"}}
        logger.setup_logging(cfg)

        try:
            assert (tmp_path / "relative.log").exists()
            assert not (elsewhere / "relative.log").exists()
        finally:
            # setup_logging attaches a FileHandler to the shared "cyclaw"
            # logger singleton -- clean it up so later tests in this process
            # don't inherit a handle on this test's deleted tmp_path file.
            import logging as _logging

            root = _logging.getLogger("cyclaw")
            for handler in list(root.handlers):
                handler.close()
                root.removeHandler(handler)
