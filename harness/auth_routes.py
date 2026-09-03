"""Harness /api/auth/* routes — session/RBAC copy of the gateway Users panel.

Extracted from harness/server.py so create_app stays factory + middleware +
wiring. Handlers are decorated directly onto the FastAPI app (not an
APIRouter): FastAPI 0.138's include_router hides sub-router routes from
app.routes introspection — same reason gate_ops.py / harness/agent_routes.py
register by name.

Invariant I6: this module never imports agentic/, sync/, guardrails/, or the
core six. Auth primitives come from utils.authn* (already the harness's
source); HTTP status/cookie names are looked up on harness.server at request
time so existing tests keep working.

The dependency lists (auth_open / auth_sess / rate-limit-only) stay owned by
create_app — they close over that instance's limiter and CSRF token — and are
injected unchanged. Fail-closed when auth is off (503 AUTH_DISABLED), matching
gate_auth.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from schemas.api import (
    AuthCreateUserRequest,
    AuthLoginRequest,
    AuthSetPasswordRequest,
    AuthSetRoleRequest,
    AuthSetupStatusResponse,
)
from utils.authn import PasswordPolicyError, validate_role
from utils.authn_manager import BOOTSTRAP_USERNAME
from utils.errors import AuthBootstrapComplete, AuthLastAdmin, AuthUserExists, AuthUserNotFound


def register_auth_routes(
    app: FastAPI,
    *,
    harness_auth: Any,
    auth_open: list[Any],
    auth_sess: list[Any],
    rate_limit_only: list[Any],
) -> None:
    """Register /api/auth/* on ``app`` with create_app's deps injected."""
    # Late import: register_auth_routes is called from create_app after
    # harness.server has finished importing this module.
    from harness import server as hs

    def _require_harness_auth():
        if harness_auth is None:
            raise HTTPException(
                status_code=hs._HTTP_UNAVAILABLE,
                detail={
                    hs._CODE_KEY: "AUTH_DISABLED",
                    hs._MESSAGE_KEY: "authentication is not enabled",
                    hs._DETAILS_KEY: {},
                },
            )
        return harness_auth

    def _auth_http(status: int, code: str, message: str) -> HTTPException:
        return HTTPException(
            status_code=status,
            detail={hs._CODE_KEY: code, hs._MESSAGE_KEY: message, hs._DETAILS_KEY: {}},
        )

    def _raise_auth_error(exc: Exception) -> None:
        # Local copy of gate_auth._raise_auth_error -- I6 forbids importing it.
        if isinstance(exc, PasswordPolicyError):
            raise _auth_http(hs._HTTP_UNPROCESSABLE, "AUTH_POLICY", str(exc)) from exc
        if isinstance(exc, AuthUserNotFound):
            raise _auth_http(hs._HTTP_NOT_FOUND, exc.code, exc.message) from exc
        if isinstance(exc, AuthLastAdmin):
            raise _auth_http(hs._HTTP_FORBIDDEN, exc.code, exc.message) from exc
        raise exc

    def _harness_actor(request: Request):
        manager = _require_harness_auth()
        token = request.cookies.get(hs.HARNESS_SESSION_COOKIE) or ""
        session_info = manager.validate_session(token)
        if session_info is None:
            raise _auth_http(hs._HTTP_UNAUTHORIZED, "AUTH_REQUIRED", "authentication required")
        account = manager.get_user(session_info.username)
        if account is None:
            raise _auth_http(hs._HTTP_UNAUTHORIZED, "AUTH_REQUIRED", "authentication required")
        return account

    def _require_user_admin(account) -> None:
        if account.role not in {hs._ROLE_ADMIN, hs._ROLE_OPERATOR}:
            raise _auth_http(hs._HTTP_FORBIDDEN, hs._PERM_DENIED, hs._DENIED_MSG)

    def _user_payload(account) -> dict:
        return {
            hs._USERNAME_KEY: account.username,
            hs._ROLE_KEY: account.role,
            "disabled": account.disabled,
            "created_ts": account.created_ts,
            "last_login_ts": account.last_login_ts,
            "locked": account.locked_until_ts is not None,
        }

    @app.get("/api/auth/setup-status", dependencies=rate_limit_only)
    def harness_setup_status() -> AuthSetupStatusResponse:
        manager = _require_harness_auth()
        pending = manager.needs_password_setup()
        return AuthSetupStatusResponse(
            enabled=True,
            needs_password=pending,
            username=BOOTSTRAP_USERNAME if pending else None,
        )

    @app.post("/api/auth/bootstrap-password", dependencies=auth_open)
    def harness_bootstrap_password(
        request: Request, response: Response, req: AuthSetPasswordRequest
    ) -> dict:
        manager = _require_harness_auth()
        if not hs._is_loopback_peer(request) or hs._looks_proxied(request):
            raise _auth_http(
                hs._HTTP_FORBIDDEN,
                "AUTH_LOOPBACK_ONLY",
                "first password must be set from this machine "
                "without reverse-proxy forwarding headers",
            )
        try:
            login_result = manager.bootstrap_set_password(req.password)
        except AuthBootstrapComplete as exc:
            raise _auth_http(hs._HTTP_CONFLICT, exc.code, exc.message) from exc
        except PasswordPolicyError as exc:
            raise _auth_http(hs._HTTP_UNPROCESSABLE, "AUTH_POLICY", str(exc)) from exc
        response.set_cookie(
            key=hs.HARNESS_SESSION_COOKIE,
            value=login_result.session_id,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return {
            hs._USERNAME_KEY: login_result.username,
            hs._ROLE_KEY: hs._ROLE_ADMIN,
            "csrf_token": login_result.csrf_token,
        }

    @app.post("/api/auth/login", dependencies=auth_open)
    async def harness_login(req: AuthLoginRequest, response: Response) -> dict:
        from utils.errors import AuthAccountLocked, AuthLoginFailed

        manager = _require_harness_auth()
        try:
            login_result = manager.login(req.username, req.password)
        except AuthAccountLocked as exc:
            raise _auth_http(hs._HTTP_LOCKED, exc.code, exc.message) from exc
        except AuthLoginFailed as exc:
            raise _auth_http(hs._HTTP_UNAUTHORIZED, exc.code, exc.message) from exc
        response.set_cookie(
            key=hs.HARNESS_SESSION_COOKIE,
            value=login_result.session_id,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        account = manager.get_user(login_result.username)
        role = hs._ROLE_OPERATOR if account is None else account.role
        return {
            hs._USERNAME_KEY: login_result.username,
            hs._ROLE_KEY: role,
            "csrf_token": login_result.csrf_token,
        }

    @app.post("/api/auth/logout", dependencies=auth_sess)
    def harness_logout(request: Request, response: Response) -> dict:
        manager = _require_harness_auth()
        token = request.cookies.get(hs.HARNESS_SESSION_COOKIE)
        if token:
            manager.logout(token)
        response.delete_cookie(key=hs.HARNESS_SESSION_COOKIE, path="/")
        return {hs._OK_KEY: True}

    @app.get("/api/auth/whoami", dependencies=auth_open)
    def harness_whoami(request: Request) -> dict:
        account = _harness_actor(request)
        return {hs._USERNAME_KEY: account.username, hs._ROLE_KEY: account.role}

    @app.get("/api/auth/users", dependencies=auth_open)
    def harness_list_users(request: Request) -> list:
        account = _harness_actor(request)
        _require_user_admin(account)
        manager = _require_harness_auth()
        return [_user_payload(row) for row in manager.list_users()]

    @app.post("/api/auth/users", dependencies=auth_sess)
    def harness_create_user(request: Request, req: AuthCreateUserRequest) -> dict:
        account = _harness_actor(request)
        _require_user_admin(account)
        # Canonicalize BEFORE the operator/admin comparison -- comparing the
        # raw request string let "Admin"/"ADMIN"/"admin " skip this guard
        # entirely (req.role == _ROLE_ADMIN is a literal-string ==), then get
        # stored as canonical "admin" once create_user() re-validates it.
        # gate_auth.py's auth_create_user does this in the correct order;
        # mirror it here.
        try:
            role = validate_role(req.role)
        except PasswordPolicyError as exc:
            raise _auth_http(hs._HTTP_UNPROCESSABLE, "AUTH_POLICY", str(exc)) from exc
        if account.role == hs._ROLE_OPERATOR and role == hs._ROLE_ADMIN:
            raise _auth_http(hs._HTTP_FORBIDDEN, hs._PERM_DENIED, hs._DENIED_MSG)
        manager = _require_harness_auth()
        # gate_auth.py's auth_create_user turns AuthUserExists into a 409; this
        # copy caught nothing, so a duplicate username escaped as an unhandled
        # 500 (harness/server.py registers only a RequestValidationError
        # handler). No race needed -- the ordinary second create hit it too.
        try:
            created = manager.create_user(req.username, req.password, role)
        except AuthUserExists as exc:
            raise _auth_http(hs._HTTP_CONFLICT, "AUTH_USER_EXISTS", exc.message) from exc
        new_user = manager.get_user(created)
        if new_user is None:
            raise _auth_http(hs._HTTP_UNAVAILABLE, "AUTH_ERROR", "created user missing")
        return _user_payload(new_user)

    @app.post("/api/auth/users/{username}/password", dependencies=auth_sess)
    def harness_set_password(request: Request, username: str, req: AuthSetPasswordRequest) -> dict:
        account = _harness_actor(request)
        manager = _require_harness_auth()
        target = manager.get_user(username)
        if target is None:
            raise _auth_http(hs._HTTP_NOT_FOUND, "AUTH_USER_NOT_FOUND", "unknown user")
        operator_blocked = account.role == hs._ROLE_OPERATOR and target.role == hs._ROLE_ADMIN
        if account.role != hs._ROLE_ADMIN and operator_blocked:
            raise _auth_http(hs._HTTP_FORBIDDEN, hs._PERM_DENIED, hs._DENIED_MSG)
        if account.role not in {hs._ROLE_ADMIN, hs._ROLE_OPERATOR}:
            raise _auth_http(hs._HTTP_FORBIDDEN, hs._PERM_DENIED, hs._DENIED_MSG)
        try:
            manager.set_password(username, req.password)
        except Exception as exc:
            _raise_auth_error(exc)
        return {hs._OK_KEY: True}

    @app.post("/api/auth/users/{username}/role", dependencies=auth_sess)
    def harness_set_role(request: Request, username: str, req: AuthSetRoleRequest) -> dict:
        account = _harness_actor(request)
        if account.role != hs._ROLE_ADMIN:
            raise _auth_http(hs._HTTP_FORBIDDEN, hs._PERM_DENIED, hs._DENIED_MSG)
        try:
            _require_harness_auth().set_role(username, req.role)
        except Exception as exc:
            _raise_auth_error(exc)
        return {hs._OK_KEY: True}

    @app.delete("/api/auth/users/{username}", dependencies=auth_sess)
    def harness_delete_user(request: Request, username: str) -> dict:
        account = _harness_actor(request)
        if account.role != hs._ROLE_ADMIN:
            raise _auth_http(hs._HTTP_FORBIDDEN, hs._PERM_DENIED, hs._DENIED_MSG)
        try:
            _require_harness_auth().delete_user(username)
        except Exception as exc:
            _raise_auth_error(exc)
        return {hs._OK_KEY: True}
