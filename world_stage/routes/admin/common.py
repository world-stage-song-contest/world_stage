
from flask import Blueprint, redirect, request

from ...db import get_db
from ...utils import (
    get_user_role_from_session,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def _require_admin():
    permissions = get_user_role_from_session(request.cookies.get("session"))
    moderator_endpoints = {
        "admin.messages_inbox",
        "admin.verifications",
        "admin.verifications_special",
        "admin.add_verification_comment",
        "admin.set_verification_status",
        "admin.merge_replaced_song",
        "admin.hide_verification_revision",
        "admin.add_verification_comment_special",
        "admin.set_verification_status_special",
        "admin.merge_replaced_song_special",
        "admin.hide_verification_revision_special",
    }
    if not permissions.can_view_restricted and not (
        permissions.can_moderate and request.endpoint in moderator_endpoints
    ):
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
        SELECT id, status, submissions_open, scoreboard_style,
               special_name, special_short_name
        FROM year
        WHERE special_short_name = %s
        """,
        (short_name,),
    )
    return cursor.fetchone()
