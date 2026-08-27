"""Contract tests for static/harness.html.

The harness console is a separate static file from terminal.html/terminal.js,
but it talks to the same gateway. These tests pin behaviors that are invisible
to server-side pytest but easy to regress in client-side markup.
"""

from pathlib import Path

import gate

_HARNESS_HTML = Path(gate.__file__).resolve().parent / "static" / "harness.html"


def _source() -> str:
    return _HARNESS_HTML.read_text(encoding="utf-8")


def test_enter_handler_ignores_ime_composition():
    """Enter commits an IME candidate; sending on that keystroke submits a
    half-composed query. terminal.js already guards this; harness.html must too."""
    html = _source()
    handler = html.split("input.addEventListener('keydown'", 1)
    assert len(handler) == 2, "the harness input keydown handler moved; update this test"
    after = handler[1].split("});", 1)[0]
    assert "if (e.isComposing) return;" in after
    assert after.index("if (e.isComposing) return;") < after.index("if (e.key === 'Enter')"), (
        "the composition check must precede the Enter branch"
    )


def test_status_polling_is_visibility_aware():
    """harness.html already pauses status polling when the tab is hidden.
    This guards the implementation against accidental regression."""
    html = _source()
    assert "statusVisible" in html
    assert "document.addEventListener('visibilitychange'" in html
    assert "scheduleStatusRefresh()" in html
