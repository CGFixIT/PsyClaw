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
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute

from metrics import summarize_audit
from schemas.api import (
    AuthCreateUserRequest,
    AuthLoginRequest,
    AuthLoginResponse,
    AuthSetPasswordRequest,
    AuthSetRoleRequest,
    AuthSetupStatusResponse,
    AuthUserRecord,
    AuthWhoamiResponse,
)
from utils import authn
from utils.authn_manager import BOOTSTRAP_USERNAME, AuthManager, SessionInfo, UserSummary
from utils.errors import (
    AuthAccountLocked,
    AuthBootstrapComplete,
    AuthLastAdmin,
    AuthLoginFailed,
    AuthUserExists,
    AuthUserNotFound,
)

logger = logging.getLogger("cyclaw.gate_auth")

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_CONFLICT = 409
_HTTP_LOCKED = 423
_HTTP_SERVICE_UNAVAILABLE = 503
_LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost"})

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

    def _host_matches_allow_list(hostname: str) -> bool:
        """Does ``hostname`` satisfy allowed_hosts the way the middleware does?

        Mirrors starlette's TrustedHostMiddleware rule -- an exact match, or a
        leading ``*.`` domain wildcard matched by suffix. A plain ``in`` test is
        stricter than the Host filter that already admitted the request, so an
        operator who allow-lists ``*.example.com`` and is served at
        ``node.example.com`` would have their own console called cross-site.

        A bare ``"*"`` deliberately never matches: it makes the middleware skip
        Host validation entirely, and an unvalidated Host is nothing for an
        Origin to be compared against.

        Duplicated from gate.py's function of the same name rather than
        imported, for the same reason tls_enabled above is duplicated: gate.py
        imports THIS module, so the dependency cannot run the other way.
        """
        for pattern in allowed_hosts:
            if pattern == "*":
                continue
            if hostname == pattern or (pattern.startswith("*.") and hostname.endswith(pattern[1:])):
                return True
        return False

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
        try:
            parsed = urlparse(origin)
        except ValueError:
            # A structurally malformed Origin (e.g. an unbalanced IPv6
            # bracket: "http://[evil") makes urlparse() itself raise, not
            # merely a lazy .hostname/.port access. It can never legitimately
            # be this request's own origin, so treat it as an ordinary
            # cross-origin mismatch -- attacker-controlled on an
            # unauthenticated route, so this must fail closed rather than let
            # the exception escape as a 500.
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CROSS_ORIGIN_BLOCKED",
                    _MESSAGE_KEY: "Cross-origin request rejected",
                    _DETAILS_KEY: {},
                },
            ) from None
        try:
            # .port is a lazy property: a well-formed urlparse() result can
            # still raise here, for a non-numeric or out-of-range (>65535)
            # port string -- e.g. Origin: http://localhost:notaport or
            # http://localhost:99999. (A structurally malformed Origin raises
            # earlier, at the urlparse() call itself, guarded above.) Both
            # are attacker-controlled on an unauthenticated route, so an
            # uncaught ValueError here would turn a malformed cross-origin
            # request into a 500 instead of the 403 this check exists to
            # return.
            origin_port = parsed.port
        except ValueError:
            # Refuse outright rather than falling back to None. None is not a
            # neutral value here -- it is what request.url.port ITSELF reads as
            # whenever the server was reached on a scheme-default port, which
            # is the TLS deployment this module exists for. On :443 a None
            # fallback made "https://host:notaport" compare EQUAL to the target
            # and pass as same-origin (issue #1201).
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CROSS_ORIGIN_BLOCKED",
                    _MESSAGE_KEY: "Cross-origin request rejected",
                    _DETAILS_KEY: {},
                },
            ) from None
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
            and _host_matches_allow_list(parsed.hostname)
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
        # session.csrf_token is the stored HASH (see authn_manager.SessionInfo's
        # docstring), never the plaintext -- hash the header value the same
        # way before comparing.
        supplied = request.headers.get(_CSRF_HEADER, "")
        if not hmac.compare_digest(authn.hash_token(supplied).encode("utf-8"), session.csrf_token.encode("utf-8")):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CSRF_TOKEN_INVALID",
                    _MESSAGE_KEY: "missing or invalid CSRF token",
                    _DETAILS_KEY: {},
                },
            )
        return session

    async def require_session_or_token(
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

        Async with ``to_thread`` around the manager calls: as a sync
        dependency FastAPI would run this in the threadpool anyway; making
        that explicit keeps the sqlite lookups off the event loop now that
        the failure path awaits the limiter and the audit log.

        The 401 path is rate-limited and audited HERE, in the dependency
        (PR #940 review findings 2 and 5): ``/query``'s per-IP limiter lives
        in the endpoint BODY, and route dependencies run before the body, so
        an unauthenticated flood would otherwise be an un-throttled,
        unrecorded DB lookup per request. The success path deliberately does
        NOT call the limiter -- the endpoint body already counts exactly
        once, and counting here too would halve the configured budget.
        """
        manager = _require_enabled()
        username: str | None = None
        if cyclaw_session:
            session_info = await asyncio.to_thread(manager.validate_session, cyclaw_session)
            if session_info is not None:
                username = session_info.username
        if username is None and authorization and authorization.lower().startswith(_BEARER_PREFIX):
            token = authorization[len(_BEARER_PREFIX):].strip()
            username = await asyncio.to_thread(manager.verify_device_token, token)
        if username is None:
            await enforce_rate_limit(request)
            await audit({
                _EVENT_KEY: "auth_credential_rejected",
                "path": request.url.path,
            })
            raise HTTPException(
                status_code=_HTTP_UNAUTHORIZED,
                detail={_CODE_KEY: "AUTH_REQUIRED", _MESSAGE_KEY: "authentication required", _DETAILS_KEY: {}},
            )
        request.state.auth_username = username
        return username

    def _client_is_loopback(request: Request) -> bool:
        host = request.client.host if request.client else ""
        if host in _LOOPBACK_CLIENTS:
            return True
        try:
            import ipaddress

            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @app.get("/auth/setup-status", dependencies=[Depends(enforce_rate_limit)])
    async def auth_setup_status() -> AuthSetupStatusResponse:
        manager = _require_enabled()
        pending = await asyncio.to_thread(manager.needs_password_setup)
        return AuthSetupStatusResponse(
            enabled=True,
            needs_password=pending,
            username=BOOTSTRAP_USERNAME if pending else None,
        )

    @app.post(
        "/auth/bootstrap-password",
        dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)],
    )
    async def auth_bootstrap_password(
        request: Request, response: Response, req: AuthSetPasswordRequest
    ) -> AuthLoginResponse:
        manager = _require_enabled()
        if not _client_is_loopback(request):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "AUTH_LOOPBACK_ONLY",
                    _MESSAGE_KEY: "first password must be set from this machine",
                    _DETAILS_KEY: {},
                },
            )
        try:
            login_result = await asyncio.to_thread(manager.bootstrap_set_password, req.password)
        except AuthBootstrapComplete as exc:
            raise HTTPException(
                status_code=_HTTP_CONFLICT,
                detail={_CODE_KEY: exc.code, _MESSAGE_KEY: exc.message, _DETAILS_KEY: exc.details or {}},
            ) from exc
        except authn.PasswordPolicyError as exc:
            raise HTTPException(
                status_code=422,
                detail={_CODE_KEY: "AUTH_POLICY", _MESSAGE_KEY: str(exc), _DETAILS_KEY: {}},
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
        await audit({
            _EVENT_KEY: "auth_bootstrap_password_set",
            "username": login_result.username,
        })
        return AuthLoginResponse(
            username=login_result.username,
            csrf_token=login_result.csrf_token,
            expires_ts=login_result.expires_ts,
        )

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
    async def auth_whoami(
        username: str = Depends(require_session_or_token),
        cyclaw_session: str | None = Cookie(default=None),
    ) -> AuthWhoamiResponse:
        manager = _require_enabled()
        user = manager.get_user(username)
        role = user.role if user is not None else authn.DEFAULT_ROLE
        # Rotate only for a cookie that already authenticated as this user.
        # Device-token whoami must not mint a CSRF -- there is no browser to
        # hold it, and rotating would invalidate a concurrent console tab
        # that shares the account. The plaintext cannot be re-read from the
        # row (hash only), so a reload without this rotate leaves logout and
        # Users writes 403 while the UI still says logged in.
        csrf_token: str | None = None
        if cyclaw_session:
            session_info = await asyncio.to_thread(manager.validate_session, cyclaw_session)
            if session_info is not None and session_info.username == username:
                csrf_token = await asyncio.to_thread(manager.rotate_csrf, cyclaw_session)
        return AuthWhoamiResponse(username=username, role=role, csrf_token=csrf_token)

    def _record_from_user(user: UserSummary) -> AuthUserRecord:
        return AuthUserRecord(
            username=user.username,
            role=user.role,
            disabled=user.disabled,
            created_ts=user.created_ts,
            last_login_ts=user.last_login_ts,
            locked=user.locked_until_ts is not None,
        )

    def _raise_auth_error(exc: Exception) -> None:
        if isinstance(exc, AuthLastAdmin):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={_CODE_KEY: exc.code, _MESSAGE_KEY: exc.message, _DETAILS_KEY: exc.details or {}},
            ) from exc
        if isinstance(exc, AuthUserExists):
            raise HTTPException(
                status_code=409,
                detail={_CODE_KEY: exc.code, _MESSAGE_KEY: exc.message, _DETAILS_KEY: exc.details or {}},
            ) from exc
        if isinstance(exc, AuthUserNotFound):
            raise HTTPException(
                status_code=404,
                detail={_CODE_KEY: exc.code, _MESSAGE_KEY: exc.message, _DETAILS_KEY: exc.details or {}},
            ) from exc
        if isinstance(exc, authn.PasswordPolicyError):
            raise HTTPException(
                status_code=422,
                detail={_CODE_KEY: "AUTH_POLICY", _MESSAGE_KEY: str(exc), _DETAILS_KEY: {}},
            ) from exc
        raise exc

    def _user_from_identity(username: str) -> UserSummary:
        manager = _require_enabled()
        user = manager.get_user(username)
        if user is None:
            raise HTTPException(
                status_code=_HTTP_UNAUTHORIZED,
                detail={_CODE_KEY: "AUTH_REQUIRED", _MESSAGE_KEY: "authentication required", _DETAILS_KEY: {}},
            )
        return user

    def _require_write_actor(
        request: Request,
        cyclaw_session: str | None = Cookie(default=None),
        authorization: str | None = Header(default=None),
    ) -> UserSummary:
        manager = _require_enabled()
        if cyclaw_session:
            session_info = manager.validate_session(cyclaw_session)
            if session_info is not None:
                # Same hash-then-compare as _enforce_csrf above.
                supplied = request.headers.get(_CSRF_HEADER, "")
                if not hmac.compare_digest(
                    authn.hash_token(supplied).encode("utf-8"), session_info.csrf_token.encode("utf-8")
                ):
                    raise HTTPException(
                        status_code=_HTTP_FORBIDDEN,
                        detail={
                            _CODE_KEY: "CSRF_TOKEN_INVALID",
                            _MESSAGE_KEY: "missing or invalid CSRF token",
                            _DETAILS_KEY: {},
                        },
                    )
                return _user_from_identity(session_info.username)
        if authorization and authorization.lower().startswith(_BEARER_PREFIX):
            token = authorization[len(_BEARER_PREFIX):].strip()
            username = manager.verify_device_token(token)
            if username is None:
                raise HTTPException(
                    status_code=_HTTP_UNAUTHORIZED,
                    detail={_CODE_KEY: "AUTH_REQUIRED", _MESSAGE_KEY: "authentication required", _DETAILS_KEY: {}},
                )
            user = _user_from_identity(username)
            if user.role != "admin":
                raise HTTPException(
                    status_code=_HTTP_FORBIDDEN,
                    detail={
                        _CODE_KEY: "AUTH_PERMISSION_DENIED",
                        _MESSAGE_KEY: "bearer admin writes require an admin token",
                        _DETAILS_KEY: {},
                    },
                )
            return user
        raise HTTPException(
            status_code=_HTTP_UNAUTHORIZED,
            detail={_CODE_KEY: "AUTH_REQUIRED", _MESSAGE_KEY: "authentication required", _DETAILS_KEY: {}},
        )

    def _assert_can_list(actor: UserSummary) -> None:
        if actor.role not in {"admin", "operator"}:
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={_CODE_KEY: "AUTH_PERMISSION_DENIED", _MESSAGE_KEY: "users list denied", _DETAILS_KEY: {}},
            )

    def _assert_can_create(actor: UserSummary, new_role: str) -> None:
        if actor.role == "admin" and new_role in authn.ROLES:
            return
        if actor.role == "operator" and new_role in {"operator", "audit"}:
            return
        raise HTTPException(
            status_code=_HTTP_FORBIDDEN,
            detail={_CODE_KEY: "AUTH_PERMISSION_DENIED", _MESSAGE_KEY: "create user denied", _DETAILS_KEY: {}},
        )

    def _assert_can_touch(
        actor: UserSummary, target: UserSummary, *, delete: bool = False, set_role: bool = False,
    ) -> None:
        if actor.role == "admin":
            return
        if actor.role == "operator":
            if delete or set_role or target.role == "admin":
                raise HTTPException(
                    status_code=_HTTP_FORBIDDEN,
                    detail={
                        _CODE_KEY: "AUTH_PERMISSION_DENIED",
                        _MESSAGE_KEY: "operator cannot perform this action",
                        _DETAILS_KEY: {},
                    },
                )
            return
        raise HTTPException(
            status_code=_HTTP_FORBIDDEN,
            detail={
                _CODE_KEY: "AUTH_PERMISSION_DENIED",
                _MESSAGE_KEY: "admin write denied",
                _DETAILS_KEY: {},
            },
        )

    @app.get("/auth/users", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_list_users(username: str = Depends(require_session_or_token)) -> list[AuthUserRecord]:
        actor = _user_from_identity(username)
        _assert_can_list(actor)
        manager = _require_enabled()
        users = await asyncio.to_thread(manager.list_users)
        return [_record_from_user(u) for u in users]

    @app.post("/auth/users", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_create_user(
        request: Request, req: AuthCreateUserRequest, actor: UserSummary = Depends(_require_write_actor),
    ) -> AuthUserRecord:
        try:
            role = authn.validate_role(req.role)
        except authn.PasswordPolicyError as exc:
            _raise_auth_error(exc)
        _assert_can_create(actor, role)
        manager = _require_enabled()
        try:
            created = await asyncio.to_thread(manager.create_user, req.username, req.password, role)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_user_created", "username": actor.username, "target": created, "role": role})
        user = manager.get_user(created)
        if user is None:
            raise HTTPException(
                status_code=500,
                detail={_CODE_KEY: "AUTH_ERROR", _MESSAGE_KEY: "created user missing", _DETAILS_KEY: {}},
            )
        return _record_from_user(user)

    @app.post(
        "/auth/users/{username}/password",
        dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)],
    )
    async def auth_set_password(
        username: str, req: AuthSetPasswordRequest, actor: UserSummary = Depends(_require_write_actor),
    ) -> dict[str, bool]:
        manager = _require_enabled()
        target = manager.get_user(username)
        if target is None:
            _raise_auth_error(AuthUserNotFound(f"unknown user: {username}", details={"username": username}))
        _assert_can_touch(actor, target)
        try:
            await asyncio.to_thread(manager.set_password, username, req.password)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_password_reset", "username": actor.username, "target": target.username})
        return {"ok": True}

    @app.post("/auth/password", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_set_own_password(
        req: AuthSetPasswordRequest, actor: UserSummary = Depends(_require_write_actor),
    ) -> dict[str, bool]:
        manager = _require_enabled()
        try:
            await asyncio.to_thread(manager.set_password, actor.username, req.password)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_password_self", "username": actor.username, "target": actor.username})
        return {"ok": True}

    @app.post("/auth/users/{username}/role", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_set_role(
        username: str, req: AuthSetRoleRequest, actor: UserSummary = Depends(_require_write_actor),
    ) -> dict[str, bool]:
        if actor.role != "admin":
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "AUTH_PERMISSION_DENIED",
                    _MESSAGE_KEY: "only admin can set roles",
                    _DETAILS_KEY: {},
                },
            )
        try:
            role = authn.validate_role(req.role)
            await asyncio.to_thread(_require_enabled().set_role, username, role)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_role_set", "username": actor.username, "target": username, "role": req.role})
        return {"ok": True}

    @app.post(
        "/auth/users/{username}/disable",
        dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)],
    )
    async def auth_disable_user(
        username: str, actor: UserSummary = Depends(_require_write_actor),
    ) -> dict[str, bool]:
        manager = _require_enabled()
        target = manager.get_user(username)
        if target is None:
            _raise_auth_error(AuthUserNotFound(f"unknown user: {username}", details={"username": username}))
        _assert_can_touch(actor, target)
        try:
            await asyncio.to_thread(manager.disable_user, username)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_user_disabled", "username": actor.username, "target": target.username})
        return {"ok": True}

    @app.post(
        "/auth/users/{username}/enable",
        dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)],
    )
    async def auth_enable_user(
        username: str, actor: UserSummary = Depends(_require_write_actor),
    ) -> dict[str, bool]:
        manager = _require_enabled()
        target = manager.get_user(username)
        if target is None:
            _raise_auth_error(AuthUserNotFound(f"unknown user: {username}", details={"username": username}))
        _assert_can_touch(actor, target)
        try:
            await asyncio.to_thread(manager.enable_user, username)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_user_enabled", "username": actor.username, "target": target.username})
        return {"ok": True}

    @app.delete("/auth/users/{username}", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_delete_user(
        username: str, actor: UserSummary = Depends(_require_write_actor),
    ) -> dict[str, bool]:
        if actor.role != "admin":
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "AUTH_PERMISSION_DENIED",
                    _MESSAGE_KEY: "only admin can delete users",
                    _DETAILS_KEY: {},
                },
            )
        manager = _require_enabled()
        try:
            await asyncio.to_thread(manager.delete_user, username)
        except Exception as exc:
            _raise_auth_error(exc)
        await audit({_EVENT_KEY: "auth_user_deleted", "username": actor.username, "target": username})
        return {"ok": True}

    @app.get("/auth/audit/summary", dependencies=[Depends(enforce_rate_limit), Depends(_enforce_same_origin)])
    async def auth_audit_summary(username: str = Depends(require_session_or_token)) -> dict:
        actor = _user_from_identity(username)
        if actor.role not in {"admin", "audit"}:
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={_CODE_KEY: "AUTH_PERMISSION_DENIED", _MESSAGE_KEY: "audit view denied", _DETAILS_KEY: {}},
            )
        repo_root = Path(__file__).resolve().parent
        audit_file = str(repo_root / (cfg.get("logging") or {}).get("audit_file", "logs/audit.jsonl"))
        return await asyncio.to_thread(summarize_audit, audit_file)

    return require_session_or_token
