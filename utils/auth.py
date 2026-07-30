"""Fail-closed Bearer API-key auth for CyClaw's operator control planes.

This is a deliberate SECOND implementation of the check ``gate.py`` performs
inline, not a refactor of it. ``harness/`` cannot import ``gate`` --
``tests/test_harness_isolation.py`` enforces that at the AST level -- and moving
gate.py onto a shared module would break four separate source-text checks that
grep gate.py itself for ``require_api_key`` / ``hmac.compare_digest`` /
``CYCLAW_API_KEY`` (invariant-guard's G2, the sandbox verifier, and
``tests/test_security.py``'s direct import). So gate.py keeps its copy and this
module serves the harness. ``tests/test_harness_auth.py`` asserts the two behave
identically, which is what keeps them from drifting.

One deliberate difference from gate.py's version: the 401 body uses the
``{code, message, details}`` envelope ``harness/server.py::_err`` produces,
because ``static/harness.html``'s ``api()`` helper reads ``detail.message``. A
bare-string detail (gate.py's shape) renders in that console as an opaque
"HTTP 401" with no hint that a key is needed.
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

API_KEY_ENV = "CYCLAW_API_KEY"
_HTTP_UNAUTHORIZED = 401

# auto_error=False so a missing header yields credentials=None instead of
# FastAPI's own 403. This handler then owns every failure path, which is what
# lets an unset key fail CLOSED rather than fall through as "no credentials
# required".
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(message: str, reason: str) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": message, "details": {"reason": reason}},
    )


# Annotated[...] rather than gate.py's `= Depends(...)` default. Same FastAPI
# behavior; this form is the one ruff's B008 accepts, so it needs no per-file
# ignore (gate.py carries one). Also makes the function directly callable in a
# test with a plain credentials object.
def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
) -> None:
    """Reject the request unless a correct Bearer ``CYCLAW_API_KEY`` was presented.

    Fail-closed: when the env var is unset the endpoint is REFUSED, never left
    open. No key is generated, logged, or stored.
    """
    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        raise _unauthorized(
            f"Operator endpoint disabled: {API_KEY_ENV} not set",
            "key_not_configured",
        )
    # Constant-time comparison: a plain `!=` short-circuits on the first differing
    # byte, leaking key length/prefix through response timing. Compare the UTF-8
    # BYTES, not the str -- hmac.compare_digest raises TypeError on a str operand
    # holding a non-ASCII character, and Starlette decodes the Authorization
    # header latin-1, so a pasted curly quote or accented character would escape
    # this handler as an unhandled 500 instead of the fail-closed 401 promised
    # above. The bytes overload never raises on content and keeps the
    # constant-time property.
    if not credentials or not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), api_key.encode("utf-8")
    ):
        raise _unauthorized("Invalid or missing API key", "bad_credentials")


__all__ = ["API_KEY_ENV", "require_api_key"]
