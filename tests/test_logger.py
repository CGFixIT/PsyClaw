"""Unit tests for utils/logger.py — audit logging + logging setup.

Focuses on the cwd-independence of relative logging.log_file / audit_file
config values (_anchor / _REPO_ROOT). _get_config already anchored the
config.yaml *file itself* to _REPO_ROOT; these guard the values *inside* it,
which previously stayed cwd-relative -- silent until CyClaw is launched from
a cwd other than the repo root (the same fragility gate.py's _BASE_DIR exists
to prevent for config.yaml/static/).
"""
import pathlib
import logging

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


class TestAuditLogSerializationFailure:
    """audit_log() has ~100 call sites across the repo; a non-JSON-serializable
    field or a non-string "query" value anywhere in the event dict must degrade
    to a warning like the OSError write-failure path, not raise -- same I4
    rationale as TestAuditLogWriteFailure, extended to record-building."""

    def test_non_serializable_field_does_not_raise(self, tmp_path, caplog):
        cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}
        event = {"event": "test_event", "bad_field": object()}
        with caplog.at_level("WARNING", logger="cyclaw.logger"):
            logger.audit_log(event, cfg=cfg)  # must not raise
        assert "audit_log failed to build event" in caplog.text
        assert not (tmp_path / "audit.jsonl").exists()

    def test_non_string_query_does_not_raise(self, tmp_path, caplog):
        cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}
        event = {"event": "test_event", "query": 12345}
        with caplog.at_level("WARNING", logger="cyclaw.logger"):
            logger.audit_log(event, cfg=cfg)  # must not raise
        assert "audit_log failed to build event" in caplog.text

    def test_failure_leaves_caller_dict_unmutated(self, tmp_path):
        cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}
        event = {"event": "test_event", "bad_field": object()}
        original_keys = set(event.keys())
        logger.audit_log(event, cfg=cfg)
        assert set(event.keys()) == original_keys

    def test_valid_event_still_writes_normally(self, tmp_path):
        """Regression guard: the new try block must not change the happy path."""
        cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}
        logger.audit_log({"event": "test_event", "detail": "fine"}, cfg=cfg)
        logger.close_audit_handles()
        record = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[0])
        assert record["event"] == "test_event"
        assert record["detail"] == "fine"


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


class TestThirdPartyLogCapture:
    """logging.level is DEBUG for CyClaw's own modules; third-party loggers
    reach the same file but are held at a floor.

    The gap is a security control, not a noise preference: httpcore's DEBUG
    output is wire-level and carries the Authorization header of every outbound
    Grok/Claude/Ollama call, so a single shared DEBUG level would write live API
    keys into logs/cyclaw.log.
    """

    def test_cyclaw_records_pass_below_the_floor(self):
        from utils.logger import _ThirdPartyFloor

        floor = _ThirdPartyFloor(logging.INFO)
        record = logging.LogRecord(
            "cyclaw.graph", logging.DEBUG, __file__, 1, "chunk budget", None, None
        )
        assert floor.filter(record) is True

    def test_third_party_debug_is_dropped(self):
        from utils.logger import _ThirdPartyFloor

        floor = _ThirdPartyFloor(logging.INFO)
        leaky = logging.LogRecord(
            "httpcore.http11", logging.DEBUG, __file__, 1,
            "send_request_headers.started request=<Request [b'POST']> "
            "headers=[(b'authorization', b'Bearer sk-live-secret')]",
            None, None,
        )
        assert floor.filter(leaky) is False

    def test_third_party_warnings_still_reach_the_file(self):
        """The floor holds DEBUG back; it must not silence real problems."""
        from utils.logger import _ThirdPartyFloor

        floor = _ThirdPartyFloor(logging.INFO)
        for level in (logging.INFO, logging.WARNING, logging.ERROR):
            record = logging.LogRecord("chromadb", level, __file__, 1, "x", None, None)
            assert floor.filter(record) is True

    def test_shipped_config_keeps_the_gap(self):
        """config.yaml must not set third_party_level to DEBUG. If someone
        raises it, this fails loudly rather than leaking keys quietly."""
        import yaml

        root = pathlib.Path(__file__).resolve().parents[1]
        cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        log_cfg = cfg["logging"]
        assert log_cfg["level"] == "DEBUG"
        assert log_cfg["third_party_level"] != "DEBUG"

    def test_stray_loggers_land_in_the_configured_file(self, tmp_path, monkeypatch):
        """The actual ask: a logger outside the cyclaw.* namespace must end up in
        cyclaw.log rather than only on stderr."""
        import utils.logger as logger_mod

        log_path = tmp_path / "cyclaw.log"
        monkeypatch.setattr(logger_mod, "_logging_initialized", False)
        real_root = logging.getLogger()
        before = list(real_root.handlers)
        try:
            logger_mod.setup_logging({"logging": {
                "level": "DEBUG",
                "log_file": str(log_path),
                "capture_third_party": True,
                "third_party_level": "INFO",
            }})
            logging.getLogger("chromadb.telemetry").warning("third-party line")
            logging.getLogger("cyclaw.graph").debug("cyclaw debug line")
            for handler in real_root.handlers:
                handler.flush()
            text = log_path.read_text(encoding="utf-8")
        finally:
            for handler in list(real_root.handlers):
                if handler not in before:
                    real_root.removeHandler(handler)
                    handler.close()
            logger_mod._logging_initialized = False
        assert "third-party line" in text
        assert "cyclaw debug line" in text

    def test_cyclaw_lines_are_written_to_the_file_exactly_once(self, tmp_path, monkeypatch):
        """Presence is not enough -- count it.

        _capture_third_party attaches its handler to the REAL root, and
        _ThirdPartyFloor passes cyclaw.* at any level. cyclaw.* records also
        propagate up to root. So a second FileHandler on the "cyclaw" logger
        writing the same path put every CyClaw line in the file TWICE (and held
        two fds on one file). The sibling test above only asserted `in text`, so
        it passed either way and the duplication shipped unnoticed.
        """
        import utils.logger as logger_mod

        log_path = tmp_path / "cyclaw.log"
        monkeypatch.setattr(logger_mod, "_logging_initialized", False)
        real_root = logging.getLogger()
        before = list(real_root.handlers)
        try:
            logger_mod.setup_logging({"logging": {
                "level": "DEBUG",
                "log_file": str(log_path),
                "capture_third_party": True,
                "third_party_level": "INFO",
            }})
            logging.getLogger("cyclaw.graph").info("count-me-once")
            logging.getLogger("chromadb.telemetry").warning("third-party-once")
            for handler in real_root.handlers + logging.getLogger("cyclaw").handlers:
                handler.flush()
            text = log_path.read_text(encoding="utf-8")
        finally:
            for handler in list(real_root.handlers):
                if handler not in before:
                    real_root.removeHandler(handler)
                    handler.close()
            logger_mod._logging_initialized = False
        assert text.count("count-me-once") == 1, "CyClaw line duplicated in the log file"
        assert text.count("third-party-once") == 1, "third-party line duplicated in the log file"

    def test_cyclaw_still_reaches_the_file_when_third_party_capture_is_off(
        self, tmp_path, monkeypatch,
    ):
        """The opt-out path must keep its own handler.

        With capture_third_party false, _capture_third_party attaches nothing --
        so setup_logging still has to own the file itself, or turning the
        third-party switch off would silently stop logging CyClaw to disk too.
        """
        import utils.logger as logger_mod

        log_path = tmp_path / "cyclaw.log"
        monkeypatch.setattr(logger_mod, "_logging_initialized", False)
        cyclaw_logger = logging.getLogger("cyclaw")
        real_root = logging.getLogger()
        before_root = list(real_root.handlers)
        before_cyclaw = list(cyclaw_logger.handlers)
        try:
            logger_mod.setup_logging({"logging": {
                "level": "DEBUG",
                "log_file": str(log_path),
                "capture_third_party": False,
            }})
            logging.getLogger("cyclaw.graph").info("offline-marker")
            for handler in cyclaw_logger.handlers:
                handler.flush()
            text = log_path.read_text(encoding="utf-8")
        finally:
            for logger_obj, before in ((real_root, before_root), (cyclaw_logger, before_cyclaw)):
                for handler in list(logger_obj.handlers):
                    if handler not in before:
                        logger_obj.removeHandler(handler)
                        handler.close()
            logger_mod._logging_initialized = False
        assert text.count("offline-marker") == 1
