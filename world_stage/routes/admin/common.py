
from flask import Blueprint, redirect, request

from ...db import get_db
from ...utils import (
    get_user_role_from_session,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def _require_admin():
    permissions = get_user_role_from_session(request.cookies.get("session"))
    if not permissions.can_view_restricted:
        return redirect("/")
    return None


def _resolve_special(short_name: str) -> dict | None:
    """Look up a special year by its short name. Returns the year row or None.

    Mirrors world_stage.routes.year.resolve_special, duplicated locally to
    avoid importing across blueprint modules.
    """
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, status, special_name, special_short_name
        FROM year
        WHERE special_short_name = %s
        """,
        (short_name,),
    )
    return cursor.fetchone()
