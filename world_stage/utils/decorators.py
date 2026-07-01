"""Route decorators for session/API authentication.

Each decorator resolves the caller's identity and injects it into the
view as a keyword argument, so route bodies no longer repeat the
session-cookie boilerplate. Views declare the matching parameter:

    @bp.get("/settings")
    @with_user
    def settings(user: tuple[int, str] | None): ...

Apply below the blueprint decorator (@bp.get must stay outermost).
"""

import functools
from collections.abc import Callable
from typing import Any

from flask import redirect, request, url_for

from .auth import (
    get_api_auth,
    get_session_auth,
    get_user_id_from_session,
    get_user_role_from_session,
)
from .responses import ErrorID, err, render_template
from .types import UserPermissions

type SessionUser = tuple[int, str]  # (user_id, username)


def _session_id() -> str | None:
    return request.cookies.get("session")


def with_user(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Inject user: SessionUser | None. Never rejects."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        kwargs["user"] = get_user_id_from_session(_session_id())
        return fn(*args, **kwargs)

    return wrapper


def with_permissions(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Inject permissions: UserPermissions (anonymous default). Never rejects."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        kwargs["permissions"] = get_user_role_from_session(_session_id())
        return fn(*args, **kwargs)

    return wrapper


def with_auth(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Inject user: SessionUser | None and permissions: UserPermissions,
    resolved in a single query. Never rejects."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user, permissions = get_session_auth(_session_id())
        kwargs["user"] = user
        kwargs["permissions"] = permissions
        return fn(*args, **kwargs)

    return wrapper


def require_user(
    *,
    redirect_to_login: bool = False,
    message: str = "Please log in",
    status: int = 403,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Reject anonymous or expired sessions, otherwise inject user: SessionUser.

    On failure: redirect to the login page when redirect_to_login is set,
    else render the error page with the given message and status."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_user_id_from_session(_session_id())
            if user is None:
                if redirect_to_login:
                    return redirect(url_for("session.login"))
                return render_template("error.html", error=message), status
            kwargs["user"] = user
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_permissions(
    check: Callable[[UserPermissions], bool],
    *,
    message: str = "Not authorized",
    status: int = 403,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Reject sessions whose permissions fail the predicate, otherwise
    inject permissions: UserPermissions."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            permissions = get_user_role_from_session(_session_id())
            if not check(permissions):
                return render_template("error.html", error=message), status
            kwargs["permissions"] = permissions
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_api_auth(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Authenticate via Bearer token or session cookie; respond 401
    otherwise. Injects auth: tuple[int, str, UserPermissions]."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth = get_api_auth()
        if not auth:
            return err(ErrorID.UNAUTHORIZED, "Authentication required")
        kwargs["auth"] = auth
        return fn(*args, **kwargs)

    return wrapper
