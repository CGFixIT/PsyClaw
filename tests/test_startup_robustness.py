"""Startup robustness tests for gate.py (the double-click crash fix).

When CyClaw is launched by double-clicking gate.py on Windows, two failure modes
previously made the console window vanish before any message could be read:

1. A prior python.exe still holding port 8787 (or a TIME_WAIT socket) made
   uvicorn.run() raise OSError [WinError 10048]; the unhandled traceback exited
   the process and the window closed instantly — the user retried "once or twice"
   until the port cleared.
2. cwd-relative opens of config.yaml / static/ crashed at import when the cwd was
   not the repo root.

These tests cover the new defensive helpers and the rewritten main(): port-in-use
detection, OSError/KeyboardInterrupt survival, and _BASE_DIR path anchoring. They
patch gate._serve so no real server is started.
"""

import socket

import gate


class TestPortDetection:
    def test_free_port_reports_not_in_use(self):
        # Bind an ephemeral port, then release it so we know it is free.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))  # DevSkim: ignore DS162092 - test loopback bind
        free_port = s.getsockname()[1]
        s.close()
        assert gate._is_port_in_use("127.0.0.1", free_port) is False  # DevSkim: ignore DS162092 - test loopback probe

    def test_bound_port_reports_in_use(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))  # DevSkim: ignore DS162092 - test loopback bind
        s.listen(1)
        port = s.getsockname()[1]
        try:
            assert gate._is_port_in_use("127.0.0.1", port) is True  # DevSkim: ignore DS162092 - test loopback probe
        finally:
            s.close()


class TestMainStartup:
    def test_main_skips_serve_when_port_in_use(self, monkeypatch, capsys):
        served = []
        monkeypatch.setattr(gate, "_is_port_in_use", lambda h, p: True)
        monkeypatch.setattr(gate, "_serve", lambda h, p: served.append((h, p)))
        monkeypatch.setattr(gate, "_hold_console", lambda: None)

        gate.main()

        assert served == []  # never attempted to bind
        out = capsys.readouterr().out
        assert "may already be running" in out

    def test_main_survives_oserror_from_serve(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "_is_port_in_use", lambda h, p: False)

        def boom(h, p):
            raise OSError("address already in use")

        monkeypatch.setattr(gate, "_serve", boom)
        monkeypatch.setattr(gate, "_hold_console", lambda: None)

        # Must not propagate — a double-clicked window would otherwise vanish.
        gate.main()

        out = capsys.readouterr().out
        assert "Failed to start CyClaw" in out

    def test_main_survives_keyboard_interrupt(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "_is_port_in_use", lambda h, p: False)

        def interrupt(h, p):
            raise KeyboardInterrupt

        monkeypatch.setattr(gate, "_serve", interrupt)
        monkeypatch.setattr(gate, "_hold_console", lambda: None)

        gate.main()  # clean Ctrl-C exit, no traceback

        out = capsys.readouterr().out
        assert "CyClaw stopped" in out

    def test_main_serves_on_free_port(self, monkeypatch):
        served = []
        monkeypatch.setattr(gate, "_is_port_in_use", lambda h, p: False)
        monkeypatch.setattr(gate, "_serve", lambda h, p: served.append((h, p)))
        monkeypatch.setattr(gate, "_hold_console", lambda: None)

        gate.main()

        assert served == [(gate.cfg["api"]["host"], gate.cfg["api"]["port"])]


class TestBaseDirAnchoring:
    def test_base_dir_is_absolute(self):
        assert gate._BASE_DIR.is_absolute()

    def test_bundled_resources_resolve_under_base_dir(self):
        # The config and static assets must resolve relative to the file, not the
        # process cwd, so a double-click launch from any directory still works.
        assert (gate._BASE_DIR / "config.yaml").is_absolute()
        assert (gate._BASE_DIR / "static").is_absolute()


class TestHoldConsole:
    def test_hold_console_noop_when_not_a_tty(self, monkeypatch):
        # In CI/piped runs stdin is not a TTY, so _hold_console must return
        # immediately without ever blocking on input().
        class _FakeStdin:
            def isatty(self):
                return False

        monkeypatch.setattr(gate.sys, "stdin", _FakeStdin())

        def fail(*a, **k):
            raise AssertionError("input() must not be called when stdin is not a TTY")

        monkeypatch.setattr("builtins.input", fail)
        gate._hold_console()  # returns without raising

    def test_hold_console_swallows_eof_on_tty(self, monkeypatch):
        class _Tty:
            def isatty(self):
                return True

        monkeypatch.setattr(gate.sys, "stdin", _Tty())
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: (_ for _ in ()).throw(EOFError()))
        gate._hold_console()

    def test_hold_console_swallows_keyboard_interrupt_on_tty(self, monkeypatch):
        class _Tty:
            def isatty(self):
                return True

        monkeypatch.setattr(gate.sys, "stdin", _Tty())

        def boom(*_a, **_k):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", boom)
        gate._hold_console()
