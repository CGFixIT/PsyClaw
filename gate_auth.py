"""Per-user authentication endpoints (docs/AUTHENTICATION_DESIGN.md, Stage 2+3).

``/auth/login``, ``/auth/logout``, ``/auth/whoami`` -- registered onto
gate.py's app the same way gate_ops.py registers ``/ops/*``: a registration
function taking the security callables already defined in gate.py. Auth is
core request-path functionality, not out-of-band like agentic/sync/guardrails,
but the split still keeps gate.py from growing without bound and reuses an
established pattern rather than inventing a new one.

When ``auth.enabled`` is false (the shipped default) every ``/auth/*`` route
below returns 503 rather than 404, so the routes' mere presence never
discloses whether the feature is turned on -- the same reasoning
gate_ops.py's routes stay registered regardless of ``agentic.enabled``.

``require_session_or_token`` is a closure built inside ``register_auth_routes``.
Stage 3 attaches it to ``POST /query`` only when ``auth_manager`` is not None
(via ``attach_identity_to_query``). gate.py's bind-guard probe locates it by
NAME (``_AUTH_DEPENDENCY_NAME``). A named no-op on the disabled default would
false-open a LAN bind, so the shipped app (auth off) does not carry it.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute

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


def attach_identity_to_query(app: FastAPI, identity_dep: Callable[..., str]) -> None:
    """Attach ``identity_dep`` to POST /query so the bind-guard probe can see it.

    Route-level ``dependencies=`` run for side effects; the return value is
    discarded, so ``require_session_or_token`` also stamps
    ``request.state.auth_username`` for ``query_endpoint`` to read. FastAPI
    builds ``route.dependant`` at registration time, so a late attach must
    update both ``route.dependencies`` and the dependant tree -- the probe
    walks names in that tree, not the decorator list.
    """
    depends = Depends(identity_dep)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != "/query" or "POST" not in (route.methods or set()):
            continue
        route.dependencies.append(depends)
        route.dependant.dependencies.insert(
            0,
            get_parameterless_sub_dependant(depends=depends, path=route.path_format),
        )
        return


def register_auth_routes(
    app: FastAPI,
    cfg: dict,
    audit: Callable[[dict], Awaitable[None]],
    enforce_rate_limit: Callable[[Request], Awaitable[None]],
    auth_manager: AuthManager | None,
) -> Callable[..., str]:
    """Register /auth/login, /auth/logout, /auth/whoami on ``app``.

    ``auth_manager`` is None when auth.enabled is false -- every handler
    below checks for that first (via ``_require_enabled``) and returns 503.
    Returns the ``require_session_or_token`` closure so the caller can attach
    it to ``/query`` when a manager exists.
    """
    api_cfg = cfg.get("api", {}) or {}
    tls_cfg = api_cfg.get("tls", {}) if isinstance(api_cfg, dict) else {}
    # Secure is set from config, not from the live connection's scheme: a
    # cookie issued Secure=False when TLS isn't actually configured must stay
    # that way even if some future proxy terminates TLS in front of CyClaw --
    # api.tls.enabled is the operator's explicit statement that THIS process
    # is the one serving HTTPS, which is what a browser's Secure flag means.
    #
    # `is True`, not bool(): every non-empty string is truthy, so a config
    # carrying `enabled: "false"` (quoted, which YAML parses as the STRING
    # "false") would otherwise read as enabled. gate.py's _flag_is_true reads
    # this exact key the same strict way for its bind guard, and the two must
    # not disagree -- one module treating a quoted value as ON while the other
    # treats it as OFF is how a Secure cookie ends up on a plain-HTTP socket,
    # which a browser then refuses to send back, breaking login with no error
    # to point at. Duplicated rather than imported because gate.py imports
    # THIS module, so the dependency cannot run the other way.
    tls_enabled = tls_cfg.get("enabled") is True if isinstance(tls_cfg, dict) else False
    allowed_hosts = cfg.get("security", {}).get("allowed_hosts", ["127.0.0.1", "localhost"])
    # A browser's Origin header is scheme+host+PORT, not host alone.
    # allowed_hosts / TrustedHostMiddleware both deliberately ignore port
    # (gate.py's own comment: "Host matching ignores port") because the Host
    # header can't disambiguate a reverse proxy from the app port -- but a
    # same-origin check exists precisely to catch "same host, different
    # port", so it must not inherit that same laxity: on the LAN deployment
    # this module's own docstring says gate.py may be reached from, any OTHER
    # service sharing this host/IP on a different port would otherwise sail
    # through as "same-origin". 8787 (or whatever api.port is set to) is
    # never a scheme default, so a genuine same-origin request's Origin
    # header always states the port explicitly.

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
        if origin is None:
            return
        parsed = urlparse(origin)
        try:
            # .port is a lazy property: urlparse() itself never raises, but
            # reading .port does, for a non-numeric or out-of-range (>65535)
            # port string -- e.g. Origin: http://localhost:notaport or
            # http://localhost:99999. Both are attacker-controlled on an
            # unauthenticated route, so an uncaught ValueError here would
            # turn a malformed cross-origin request into a 500 instead of the
            # 403 this check exists to return. A malformed port can never
            # equal request.url.port (an int or None, never unparseable), so
            # treating it as None (rather than re-raising) still fails the
            # comparison below and rejects the request -- it just does so as
            # CROSS_ORIGIN_BLOCKED, not a crash.
            origin_port = parsed.port
        except ValueError:
            origin_port = None
        # The host comparison is against THIS request's own Host header, not
        # against the allow-list. allowed_hosts ships with two distinct LAN
        # machines (10.0.0.111 and 10.0.0.112) alongside the loopback names, so
        # an allow-list membership test would call a page served by one of them
        # "same-origin" with a CyClaw running on the other -- which is exactly
        # the "another device on the LAN" adversary
        # docs/AUTHENTICATION_DESIGN.md §3 names, and it would reach /auth/login
        # (the one auth route with no CSRF token to fall back on, since a
        # session does not exist yet) as a login-CSRF. request.url.hostname is
        # the Host header when the client sent a well-formed one and the bound
        # server address otherwise, so it is the actual origin of this request.
        #
        # allow-list membership is kept as a second, independent condition. It
        # is not redundant: TrustedHostMiddleware honours a "*" entry by
        # skipping Host validation entirely, and an Origin of "null" parses to
        # hostname None -- both cases fail this condition rather than matching
        # an equally-unvalidated Host.
        #
        # Port and scheme are checked against request.url -- THIS request's
        # own live values -- for the same reason the host check above already
        # uses request.url.hostname rather than a config-derived expectation:
        # config.api.port/api.tls.enabled describe how the operator INTENDED
        # to run the server, not necessarily the connection this request
        # actually arrived on (a stale config, a port forward, or any other
        # config/reality drift). No proxy headers are trusted by this app
        # (gate.py runs uvicorn without proxy_headers/forwarded_allow_ips), so
        # request.url.port/.scheme reflect the real ASGI connection and are
        # not attacker-spoofable the way an X-Forwarded-* header would be.
        # Comparing Origin against a stale expectation instead of the live
        # request is precisely how a same-origin check silently stops
        # matching reality.
        same_origin = (
            parsed.hostname is not None
            and parsed.hostname == request.url.hostname
            and parsed.hostname in allowed_hosts
            and origin_port == request.url.port
            and parsed.scheme == request.url.scheme
        )
        if not same_origin:
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CROSS_ORIGIN_BLOCKED",
                    _MESSAGE_KEY: "Cross-origin request rejected",
                    _DETAILS_KEY: {"origin_host": parsed.hostname},
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
        request: Request,
        cyclaw_session: str | None = Cookie(default=None),
        authorization: str | None = Header(default=None),
    ) -> str:
        """Return the authenticated username via EITHER a live session cookie
        OR a bearer device token -- whichever the caller presented. No CSRF
        check here: this is meant for read paths (whoami and /query), and
        CSRF only ever guards state-changing requests.

        Also stamps ``request.state.auth_username`` so a route-level
        ``Depends`` on ``/query`` (return value discarded) still attributes
        the audit record.
        """
        manager = _require_enabled()
        username: str | None = None
        if cyclaw_session:
            session_info = manager.validate_session(cyclaw_session)
            if session_info is not None:
                username = session_info.username
        if username is None and authorization and authorization.lower().startswith(_BEARER_PREFIX):
            token = authorization[len(_BEARER_PREFIX):].strip()
            username = manager.verify_device_token(token)
        if username is None:
            raise HTTPException(
                status_code=_HTTP_UNAUTHORIZED,
                detail={_CODE_KEY: "AUTH_REQUIRED", _MESSAGE_KEY: "authentication required", _DETAILS_KEY: {}},
            )
        request.state.auth_username = username
        return username

    @app.post("/auth/login", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_login(request: Request, response: Response, req: AuthLoginRequest) -> AuthLoginResponse:
        manager = _require_enabled()
        client_ip = request.client.host if request.client else "unknown"
        try:
            # manager.login() is synchronous and blocking: scrypt hashing
            # (~0.1s by design, see utils/authn.py) plus a SQLite round-trip.
            # gate.py's uvicorn.run() carries no `workers=`, so this is a
            # single-process, single-event-loop deployment -- calling it
            # directly here would stall every other in-flight request
            # (/query included) for the duration of every login attempt.
            # asyncio.to_thread matches the pattern gate.py's own _audit and
            # _check_rate_limit_async already establish for exactly this
            # class of call.
            login_result = await asyncio.to_thread(manager.login, req.username, req.password)
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
        # Same reasoning as auth_login above: manager.logout() is a blocking
        # SQLite call and this handler is `async def`, so it must be
        # off-loaded rather than run directly on the event loop.
        await asyncio.to_thread(manager.logout, session.session_id)
        response.delete_cookie(key=_SESSION_COOKIE, path="/")
        await audit({_EVENT_KEY: "auth_logout", "username": session.username})
        return {"ok": True}

    @app.get("/auth/whoami", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_whoami(username: str = Depends(require_session_or_token)) -> AuthWhoamiResponse:
        return AuthWhoamiResponse(username=username)

    return require_session_or_token
