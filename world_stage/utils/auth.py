import hashlib
import secrets
from typing import LiteralString

from flask import request

from ..db import get_db
from .types import UserPermissions

_PERMISSION_SELECT: LiteralString = """
    SELECT account_role.name, account_role.can_edit, account_role.can_view_restricted"""


def _permissions_from_row(row) -> UserPermissions:
    if not row:
        return UserPermissions()
    return UserPermissions(
        role=row["name"],
        can_edit=row["can_edit"],
        can_view_restricted=row["can_view_restricted"],
    )


def get_user_id_from_session(session_id: str | None) -> tuple[int, str] | None:
    if not session_id:
        return None
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT account.id, account.username FROM session
        JOIN account ON session.user_id = account.id
        WHERE session.session_id = %s AND session.expires_at > CURRENT_TIMESTAMP
    """,
        (session_id,),
    )
    row = cursor.fetchone()
    if row:
        return (row["id"], row["username"])
    return None


def get_user_role_from_session(session_id: str | None) -> UserPermissions:
    if not session_id:
        return UserPermissions()
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        _PERMISSION_SELECT
        + """
        FROM session
        JOIN account ON session.user_id = account.id
        JOIN account_role ON account.role = account_role.name
        WHERE session.session_id = %s AND session.expires_at > CURRENT_TIMESTAMP
    """,
        (session_id,),
    )
    return _permissions_from_row(cursor.fetchone())


def get_user_permissions(user_id: int | None) -> UserPermissions:
    if user_id is None:
        return UserPermissions()
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        _PERMISSION_SELECT
        + """
        FROM account
        JOIN account_role ON account.role = account_role.name
        WHERE account.id = %s
    """,
        (user_id,),
    )
    return _permissions_from_row(cursor.fetchone())


def get_session_auth(session_id: str | None) -> tuple[tuple[int, str] | None, UserPermissions]:
    """Resolve a session cookie to ((user_id, username) | None, permissions)
    in a single query. Anonymous or expired sessions yield (None, defaults)."""
    if not session_id:
        return None, UserPermissions()
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT account.id, account.username,
               account_role.name, account_role.can_edit, account_role.can_view_restricted
        FROM session
        JOIN account ON session.user_id = account.id
        JOIN account_role ON account.role = account_role.name
        WHERE session.session_id = %s AND session.expires_at > CURRENT_TIMESTAMP
    """,
        (session_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None, UserPermissions()
    return (row["id"], row["username"]), _permissions_from_row(row)


# ── API token helpers ──────────────────────────────────────────────
def generate_api_token() -> tuple[str, bytes]:
    """Generate a new API token. Returns (plaintext_token, token_hash)."""
    token = secrets.token_urlsafe(32)
    return token, hash_api_token(token)


def hash_api_token(token: str) -> bytes:
    """Hash a plaintext API token for database lookup."""
    return hashlib.sha256(token.encode()).digest()


def get_user_from_api_token(token: str) -> tuple[int, str] | None:
    """Look up a user by API token. Returns (user_id, username) or None."""
    token_hash = hash_api_token(token)
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT account.id, account.username FROM api_token
        JOIN account ON api_token.user_id = account.id
        WHERE api_token.token_hash = %s AND account.approved
    """,
        (token_hash,),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            """
            UPDATE api_token SET last_used_at = CURRENT_TIMESTAMP
            WHERE token_hash = %s
        """,
            (token_hash,),
        )
        db.commit()
        return (row["id"], row["username"])
    return None


def get_api_auth() -> tuple[int, str, UserPermissions] | None:
    """Authenticate an API request via Bearer token or session cookie.
    Returns (user_id, username, permissions) or None."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        result = get_user_from_api_token(token)
        if result:
            user_id, username = result
            perms = get_user_permissions(user_id)
            return (user_id, username, perms)
        return None

    # Fall back to session cookie
    session_id = request.cookies.get("session")
    if session_id:
        result = get_user_id_from_session(session_id)
        if result:
            user_id, username = result
            perms = get_user_permissions(user_id)
            return (user_id, username, perms)
    return None
