
from flask import request

from ...db import get_db
from ...utils import (
    ShowData,
    UserPermissions,
    dt_now,
    get_show_id,
    render_template,
    with_permissions,
)
from .common import bp, resolve_special


def _penalty_candidates(year_id: int, show_id: int) -> list[dict]:
    """Songs in ``show_id`` whose submitter has no ``vote_set`` row for
    that show. Returns the rows already merged with the current penalty
    value so the form can pre-check anyone already penalised.
    """
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id AS song_id,
               song.title,
               song.artist,
               song.entry_number,
               song.country_id AS cc,
               country.name AS country,
               country.id AS country_id,
               account.id AS submitter_id,
               account.username AS submitter,
               song_show.penalty
        FROM song_show
        JOIN song ON song.id = song_show.song_id
        JOIN country ON country.id = song.country_id
        LEFT JOIN account ON account.id = song.submitter_id
        WHERE song_show.show_id = %s
          AND song.submitter_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM vote_set
              WHERE vote_set.show_id = %s
                AND vote_set.voter_id = song.submitter_id
          )
        ORDER BY country.name, song.entry_number
        """,
        (show_id, show_id),
    )
    return cursor.fetchall()


def _show_penalties(show_id: int) -> dict[int, int]:
    """Return ``{song_id: penalty}`` for every song in the show that
    currently has a non-zero penalty applied."""
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT song_id, penalty FROM song_show WHERE show_id = %s AND penalty > 0",
        (show_id,),
    )
    return {row["song_id"]: row["penalty"] for row in cursor.fetchall()}


def _show_max_point(show_id: int) -> int:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT MAX(point.score) AS max_score
        FROM show
        JOIN point ON point.point_system_id = show.point_system_id
        WHERE show.id = %s
        """,
        (show_id,),
    )
    row = cursor.fetchone()
    return int(row["max_score"]) if row and row["max_score"] is not None else 0


def _render_penalty(
    show_data: ShowData, year_label: str, special_short: str | None, special_name: str | None
):
    """Shared GET body for the regular and special penalty pages."""
    if not show_data.id:
        return render_template("error.html", error="Show not found"), 404
    if show_data.voting_closes is None or show_data.voting_closes > dt_now():
        return render_template(
            "error.html",
            error="Penalties can only be applied after voting has closed.",
        ), 400

    candidates = _penalty_candidates(show_data.year or 0, show_data.id)
    max_point = _show_max_point(show_data.id)

    return render_template(
        "year/penalty.html",
        year=year_label,
        special=special_short,
        special_name=special_name,
        show=show_data.short_name,
        show_name=show_data.name,
        candidates=candidates,
        max_point=max_point,
    )


def _apply_penalty(show_data: ShowData):
    """Shared POST body — checked song IDs get the max-score penalty,
    everyone else in the candidate list has theirs cleared."""
    if not show_data.id:
        return {"error": "Show not found"}, 404
    if show_data.voting_closes is None or show_data.voting_closes > dt_now():
        return {"error": "Voting hasn't closed yet"}, 400

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("song_ids", [])
    if not isinstance(raw_ids, list):
        return {"error": "song_ids must be a list"}, 400

    try:
        checked: set[int] = {int(x) for x in raw_ids}
    except (ValueError, TypeError):
        return {"error": "song_ids must be integers"}, 400

    candidates = _penalty_candidates(show_data.year or 0, show_data.id)
    candidate_ids = {row["song_id"] for row in candidates}
    # Ignore any IDs not in the eligible-list — guards against the form
    # smuggling in unrelated songs.
    checked &= candidate_ids
    cleared = candidate_ids - checked

    max_point = _show_max_point(show_data.id)

    cursor = get_db().cursor()
    if checked:
        cursor.execute(
            "UPDATE song_show SET penalty = %s "
            "WHERE show_id = %s AND song_id = ANY(%s)",
            (max_point, show_data.id, list(checked)),
        )
    if cleared:
        cursor.execute(
            "UPDATE song_show SET penalty = 0 "
            "WHERE show_id = %s AND song_id = ANY(%s)",
            (show_data.id, list(cleared)),
        )
    get_db().commit()

    return {"status": "ok", "applied": len(checked), "cleared": len(cleared)}, 200


@bp.get("/<int:year>/<show>/penalty")
@with_permissions
def show_penalty(year: int, show: str, permissions: UserPermissions):
    if not permissions.can_view_restricted:
        return render_template("error.html", error="Admins only."), 403
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    return _render_penalty(show_data, str(year), None, None)


@bp.post("/<int:year>/<show>/penalty")
@with_permissions
def show_penalty_post(year: int, show: str, permissions: UserPermissions):
    if not permissions.can_view_restricted:
        return {"error": "Admins only"}, 403
    show_data = get_show_id(show, year)
    if not show_data:
        return {"error": "Show not found"}, 404
    return _apply_penalty(show_data)


@bp.get("/special/<short_name>/<show>/penalty")
@with_permissions
def special_show_penalty(short_name: str, show: str, permissions: UserPermissions):
    if not permissions.can_view_restricted:
        return render_template("error.html", error="Admins only."), 403
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404
    show_data = get_show_id(show, special_year["id"])
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    return _render_penalty(
        show_data, short_name, short_name, special_year["special_name"]
    )


@bp.post("/special/<short_name>/<show>/penalty")
@with_permissions
def special_show_penalty_post(short_name: str, show: str, permissions: UserPermissions):
    if not permissions.can_view_restricted:
        return {"error": "Admins only"}, 403
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404
    show_data = get_show_id(show, special_year["id"])
    if not show_data:
        return {"error": "Show not found"}, 404
    return _apply_penalty(show_data)
