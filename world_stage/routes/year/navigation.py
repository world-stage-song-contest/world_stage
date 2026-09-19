from flask import request

from ...db import get_db
from ...utils import can_manage_show, get_session_auth, get_show_id
from .common import bp, get_other_shows, resolve_special


@bp.app_template_global()
def get_show_navigation(year, show: str, special: str | None = None) -> dict:
    special_year = resolve_special(special) if special else None
    year_id = special_year["id"] if special_year else int(year)
    show_data = get_show_id(show, year_id)
    if show_data is None:
        return {}
    user, permissions = get_session_auth(request.cookies.get("session"))
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT revote_eligible_at IS NOT NULL AS eligible FROM show WHERE id = %s",
        (show_data.id,),
    )
    return {
        "year": year_id,
        "special": special,
        "label": special_year["special_name"] if special_year else year_id,
        "show": show_data,
        "other_shows": get_other_shows(year_id, show),
        "can_manage": can_manage_show(show_data, user, permissions),
        "can_view_voters": show_data.status == "full" or permissions.can_view_restricted,
        "revote_eligible": cursor.fetchone()["eligible"],
    }
