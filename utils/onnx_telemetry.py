"""Post-import ONNX Runtime telemetry suppression for the optional load seams.

The env half of the ONNX story lives in ``utils/telemetry_kill.py``
(``ORT_DISABLE_TELEMETRY=1``, set before any import -- the process-lifetime
control for the non-Windows 1DS telemetry path that landed in onnxruntime
v1.29.0). This module is the API half: onnxruntime's own
``disable_telemetry_events()`` suppresses non-essential events after import,
and per the vendor's Privacy.md it cannot prevent an initialization-time event
that already fired -- which is exactly why the env var must come first and why
this call is a second layer, not a replacement. On Windows (ETW/TraceLogging),
the API is best-effort suppression only; absolute silence there requires a
``--no_telemetry`` private build CyClaw does not claim to be.

Kept out of ``utils/telemetry_kill.py`` on purpose: that module is imported at
the very top of every entry point and must stay stdlib-only -- importing
onnxruntime there would drag a heavy transitive into every process. This
module is stdlib-only at import time too; onnxruntime is touched only inside
the function, and only at the two seams where ONNX-backed construction can
actually happen: ``retrieval/vector_store.py`` (chromadb, whose default
embedding function is ONNX-backed -- CyClaw always passes precomputed vectors,
so this is belt-and-suspenders) and ``guardrails/integration.py`` (live NeMo's
fastembed base constructs real ONNX sessions).

Reviewed 2026-08-27 against
https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md,
https://github.com/microsoft/onnxruntime/releases/tag/v1.29.0, and
https://onnxruntime.ai/docs/api/python/api_summary.html.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("cyclaw.onnx_telemetry")


def suppress_onnx_telemetry(*, force_import: bool = False) -> bool:
    """Call ``onnxruntime.disable_telemetry_events()`` if it is reachable.

    Idempotent and unconditionally safe: calling it any number of times, with
    onnxruntime absent, partially importable, or lacking the API, is a no-op
    that returns ``False``. Returns ``True`` only when the vendor API was
    actually invoked.

    By default the function only acts on an onnxruntime that is ALREADY
    imported (``sys.modules``): the env var covers the not-yet-imported case,
    and forcing the import at a seam that may never construct an ONNX model
    (chromadb with precomputed embeddings) would add a heavy dependency load
    for nothing. Pass ``force_import=True`` at a seam where ONNX-backed
    construction is imminent (live NeMo / fastembed), so the API is guaranteed
    to run before the first session is created.
    """
    module = sys.modules.get("onnxruntime")
    if module is None:
        if not force_import:
            return False
        try:
            import onnxruntime as module  # noqa: PLC0415 - deliberate lazy import at the seam
        except Exception:  # noqa: BLE001 - absent/broken optional dep must never break the seam
            return False
    disable = getattr(module, "disable_telemetry_events", None)
    if not callable(disable):
        return False
    try:
        disable()
    except Exception as exc:  # noqa: BLE001 - suppression failure must never break the caller
        logger.debug("onnxruntime.disable_telemetry_events() failed: %s", exc)
        return False
    return True
