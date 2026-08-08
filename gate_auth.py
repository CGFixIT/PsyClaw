"""Per-user authentication endpoints (docs/AUTHENTICATION_DESIGN.md, Stage 2).

``/auth/login``, ``/auth/logout``, ``/auth/whoami`` -- registered onto
gate.py's app the same way gate_ops.py registers ``/ops/*``: a registration
function taking the security callables already defined in gate.py. Auth is
core request-path functionality, not out-of-band like agentic/sync/guardrails,
but the split still keeps gate.py from growing without bound and reuses an
established pattern rather than inventing a new one.

Stage 2 builds sessions, login/logout, and per-device bearer tokens. It does
NOT enforce anything: ``/query`` and the console are untouched here, exactly
as docs/AUTHENTICATION_DESIGN.md's staged table specifies -- enforcing a
credential on ``/query`` is Stage 3. When ``auth.enabled`` is false (the
shipped default) every route below returns 503 rather than 404, so the
routes' mere presence never discloses whether the feature is turned on --
the same reasoning gate_ops.py's routes stay registered regardless of
``agentic.enabled``.

``require_session_or_token`` is exported for Stage 3 to attach to ``/query``
and the console; only ``/auth/whoami`` calls it in this module today.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response

from schemas.api import AuthLoginRequest, AuthLoginResponse, AuthWhoamiResponse
from utils.authn_manager import AuthManager, SessionInfo
from utils.errors import AuthAccountLocked, AuthLoginFailed

logger = logging.getLogger("cyclaw.gate_auth")

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_LOCKED = 423
_HTTP_SERVICE_UNAVAILABLE = 503

# Cookie/header names are a single source in this module; gate_auth owns the
# whole /auth/* surface so nothing else needs to agree on these strings today.
_SESSION_COOKIE = "cyclaw_session"
_CSRF_HEADER = "x-cyclaw-csrf"
_BEARER_PREFIX = "bearer "

_CODE_KEY = "code"
_MESSAGE_KEY = "message"
_DETAILS_KEY = "details"
_EVENT_KEY = "event"


def register_auth_routes(
    app: FastAPI,
    cfg: dict,
    audit: Callable[[dict], Awaitable[None]],
    enforce_rate_limit: Callable[[Request], Awaitable[None]],
    auth_manager: AuthManager | None,
) -> None:
    """Register /auth/login, /auth/logout, /auth/whoami on ``app``.

    ``auth_manager`` is None when auth.enabled is false -- every handler
    below checks for that first (via ``_require_enabled``) and returns 503.
    """
    api_cfg = cfg.get("api", {}) or {}
    tls_cfg = api_cfg.get("tls", {}) if isinstance(api_cfg, dict) else {}
    # Secure is set from config, not from the live connection's scheme: a
    # cookie issued Secure=False when TLS isn't actually configured must stay
    # that way even if some future proxy terminates TLS in front of CyClaw --
    # api.tls.enabled is the operator's explicit statement that THIS process
    # is the one serving HTTPS, which is what a browser's Secure flag means.
    tls_enabled = bool((tls_cfg or {}).get("enabled")) if isinstance(tls_cfg, dict) else False
    allowed_hosts = cfg.get("security", {}).get("allowed_hosts", ["127.0.0.1", "localhost"])

    def _enforce_same_origin(request: Request) -> None:
        """Reject a browser-initiated cross-site request to a state-changing
        auth route. Mirrors harness/server.py's _enforce_same_origin exactly,
        parameterized by cfg's allowed_hosts rather than a hardcoded loopback
        tuple: unlike the loopback-only harness, gate.py may legitimately be
        reached from a LAN host once auth+TLS are configured (gate.py's
        _auth_and_tls_enabled).

        Absent headers are allowed on purpose, same as harness: curl,
        PowerShell, and Telegram's client send neither, and a non-browser
        client is not a CSRF vector. Every browser that can mount this attack
        sends at least Origin on a cross-origin POST.
        """
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={_CODE_KEY: "CROSS_SITE_BLOCKED", _MESSAGE_KEY: "Cross-site request rejected", _DETAILS_KEY: {}},
            )
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).hostname not in allowed_hosts:
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CROSS_ORIGIN_BLOCKED",
                    _MESSAGE_KEY: "Cross-origin request rejected",
                    _DETAILS_KEY: {"origin_host": urlparse(origin).hostname},
                },
            )

    def _require_enabled() -> AuthManager:
        if auth_manager is None:
            raise HTTPException(
                status_code=_HTTP_SERVICE_UNAVAILABLE,
                detail={
                    _CODE_KEY: "AUTH_DISABLED",
                    _MESSAGE_KEY: "authentication is not enabled",
                    _DETAILS_KEY: {},
                },
            )
        return auth_manager

    def _session_from_cookie(cyclaw_session: str | None = Cookie(default=None)) -> SessionInfo:
        manager = _require_enabled()
        session_info = manager.validate_session(cyclaw_session or "")
        if session_info is None:
            raise HTTPException(
                status_code=_HTTP_UNAUTHORIZED,
                detail={_CODE_KEY: "AUTH_SESSION_INVALID", _MESSAGE_KEY: "no valid session", _DETAILS_KEY: {}},
            )
        return session_info

    def _enforce_csrf(request: Request, session: SessionInfo = Depends(_session_from_cookie)) -> SessionInfo:
        """Reject a state-changing request that doesn't carry this session's
        CSRF token. Only applies to the cookie path: a bearer-token caller is
        not a browser and is not a CSRF vector, the same reasoning
        harness/server.py's identical dependency documents.
        """
        supplied = request.headers.get(_CSRF_HEADER, "")
        if not hmac.compare_digest(supplied.encode("utf-8"), session.csrf_token.encode("utf-8")):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CSRF_TOKEN_INVALID",
                    _MESSAGE_KEY: "missing or invalid CSRF token",
                    _DETAILS_KEY: {},
                },
            )
        return session

    def require_session_or_token(
        cyclaw_session: str | None = Cookie(default=None),
        authorization: str | None = Header(default=None),
    ) -> str:
        """Return the authenticated username via EITHER a live session cookie
        OR a bearer device token -- whichever the caller presented. No CSRF
        check here: this is meant for read paths (whoami today; /query in
        Stage 3), and CSRF only ever guards state-changing requests.
        """
        manager = _require_enabled()
        if cyclaw_session:
            session_info = manager.validate_session(cyclaw_session)
            if session_info is not None:
                return session_info.username
        if authorization and authorization.lower().startswith(_BEARER_PREFIX):
            token = authorization[len(_BEARER_PREFIX):].strip()
            username = manager.verify_device_token(token)
            if username is not None:
                return username
        raise HTTPException(
            status_code=_HTTP_UNAUTHORIZED,
            detail={_CODE_KEY: "AUTH_REQUIRED", _MESSAGE_KEY: "authentication required", _DETAILS_KEY: {}},
        )

    @app.post("/auth/login", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_login(request: Request, response: Response, req: AuthLoginRequest) -> AuthLoginResponse:
        manager = _require_enabled()
        client_ip = request.client.host if request.client else "unknown"
        try:
            login_result = manager.login(req.username, req.password)
        except AuthAccountLocked as exc:
            await audit({_EVENT_KEY: "auth_login_locked", "ip": client_ip})
            raise HTTPException(
                status_code=_HTTP_LOCKED,
                detail={
                    _CODE_KEY: exc.code, _MESSAGE_KEY: exc.message,
                    _DETAILS_KEY: {"retry_after_sec": exc.retry_after_sec},
                },
            ) from exc
        except AuthLoginFailed as exc:
            await audit({_EVENT_KEY: "auth_login_failed", "ip": client_ip})
            raise HTTPException(
                status_code=_HTTP_UNAUTHORIZED,
                detail={_CODE_KEY: exc.code, _MESSAGE_KEY: exc.message, _DETAILS_KEY: {}},
            ) from exc
        response.set_cookie(
            key=_SESSION_COOKIE,
            value=login_result.session_id,
            httponly=True,
            samesite="strict",
            secure=tls_enabled,
            path="/",
            max_age=int(manager.absolute_timeout_sec),
        )
        await audit({_EVENT_KEY: "auth_login_ok", "ip": client_ip, "username": login_result.username})
        return AuthLoginResponse(
            username=login_result.username,
            csrf_token=login_result.csrf_token,
            expires_ts=login_result.expires_ts,
        )

    @app.post("/auth/logout", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_logout(response: Response, session: SessionInfo = Depends(_enforce_csrf)) -> dict[str, bool]:
        manager = _require_enabled()
        manager.logout(session.session_id)
        response.delete_cookie(key=_SESSION_COOKIE, path="/")
        await audit({_EVENT_KEY: "auth_logout", "username": session.username})
        return {"ok": True}

    @app.get("/auth/whoami", dependencies=[Depends(enforce_rate_limit)])
    async def auth_whoami(username: str = Depends(require_session_or_token)) -> AuthWhoamiResponse:
        return AuthWhoamiResponse(username=username)
