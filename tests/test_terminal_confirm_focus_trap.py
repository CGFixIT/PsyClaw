"""Pin addConfirmEntry so the Tab focus-trap is live, not dead code.

A `return id` placed before the keydown listener left the trap unreachable.
This file exists so the ordering cannot regress without CI noticing.
"""

from pathlib import Path

import gate

_TERMINAL_JS = Path(gate.__file__).resolve().parent / "static" / "terminal.js"


def test_add_confirm_entry_attaches_focus_trap_before_returning() -> None:
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    fn = js.split("function addConfirmEntry(", 1)[1].split("\nfunction handleConfirm", 1)[0]
    assert "el.addEventListener('keydown'" in fn, "confirm focus-trap listener missing"
    assert fn.index("el.addEventListener('keydown'") < fn.rindex("return id"), (
        "addConfirmEntry returns before attaching the Tab focus-trap"
    )
