

from ...db import get_db
from ...utils import (
    UserPermissions,
    get_show_id,
    render_template,
    with_permissions,
)
from .common import bp, resolve_special


def get_voter_participation(
    year_id: int, allowed_shows: list[str] | None = None
) -> tuple[list[str], list[dict]]:
    """Cross-show participation table for a year.

    Returns ``(show_short_names, rows)`` where each row is
    ``{"username": str, "cells": {short_name: state}}`` and ``state`` is
    one of:

    - ``"voted-entry"`` — voted AND had a song in that show.
    - ``"voted"`` — voted but had no song in that show.
    - ``"missed"`` — had a song in that show but didn't vote.
    - ``"none"`` — neither voted nor had an entry.

    A user is included if they voted in (or had a non-placeholder entry
    in) any of the included shows. Rows are sorted alphabetically by
    username (case-insensitive).

    ``allowed_shows`` optionally restricts the table to a specific subset
    of show short_names — used to hide shows whose results aren't
    published yet from non-admin viewers. ``None`` means include every
    show in the year.
    """
    db = get_db()
    cursor = db.cursor()

    if allowed_shows is None:
        cursor.execute(
            "SELECT short_name FROM show WHERE year_id = %s ORDER BY date, id",
            (year_id,),
        )
        short_names = [row["short_name"] for row in cursor.fetchall()]
    else:
        # Preserve the chronological order returned by the canonical query
        # while honouring the caller's allow-list.
        cursor.execute(
            "SELECT short_name FROM show WHERE year_id = %s ORDER BY date, id",
            (year_id,),
        )
        allowed_set = set(allowed_shows)
        short_names = [
            row["short_name"]
            for row in cursor.fetchall()
            if row["short_name"] in allowed_set
        ]

    if not short_names:
        return [], []

    cursor.execute(
        """
        SELECT account.username, show.short_name
        FROM vote_set
        JOIN show ON vote_set.show_id = show.id
        JOIN account ON vote_set.voter_id = account.id
        WHERE show.year_id = %s AND show.short_name = ANY(%s)
          AND vote_set.result_mode = 'official'
        """,
        (year_id, short_names),
    )
    voted: set[tuple[str, str]] = {
        (row["username"], row["short_name"]) for row in cursor.fetchall()
    }

    cursor.execute(
        """
        SELECT DISTINCT account.username, show.short_name
        FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN show ON song_show.show_id = show.id
        JOIN account ON song.submitter_id = account.id
        WHERE show.year_id = %s AND show.short_name = ANY(%s)
          AND NOT song.is_placeholder
        """,
        (year_id, short_names),
    )
    has_entry: set[tuple[str, str]] = {
        (row["username"], row["short_name"]) for row in cursor.fetchall()
    }

    usernames = sorted(
        {u for u, _ in voted} | {u for u, _ in has_entry},
        key=str.lower,
    )

    rows: list[dict] = []
    for username in usernames:
        cells = {}
        for sn in short_names:
            user_voted = (username, sn) in voted
            user_entry = (username, sn) in has_entry
            if user_voted and user_entry:
                cells[sn] = "voted-entry"
            elif user_voted:
                cells[sn] = "voted"
            elif user_entry:
                cells[sn] = "missed"
            else:
                cells[sn] = "none"
        rows.append({"username": username, "cells": cells})

    return short_names, rows

def _render_year_voters(
    year_id: int,
    year_label: str,
    special_short: str | None,
    special_name: str | None,
    permissions: UserPermissions,
):
    """Shared body for the regular and special year-voters pages.

    Visibility:
    - Admins (``can_view_restricted``) always see every show in the year.
    - Everyone else only sees shows whose results have been published
      (``status`` is ``partial`` or ``full``). If no show in the year has
      reached that level yet, the page returns an error rather than a
      blank table.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT status FROM year WHERE id = %s", (year_id,))
    year_row = cursor.fetchone()
    if not year_row:
        return render_template("error.html", error="Year not found"), 404

    is_closed = year_row["status"] == "closed"

    allowed_shows: list[str] | None
    if permissions.can_view_restricted:
        allowed_shows = None
    else:
        cursor.execute(
            """
            SELECT short_name FROM show
            WHERE year_id = %s AND status IN ('partial', 'full')
            ORDER BY date, id
            """,
            (year_id,),
        )
        allowed_shows = [row["short_name"] for row in cursor.fetchall()]
        if not allowed_shows:
            return render_template(
                "error.html",
                error="No shows in this year have published results yet",
            ), 403

    show_short_names, rows = get_voter_participation(year_id, allowed_shows)
    return render_template(
        "year/voters_overview.html",
        year=year_label,
        special=special_short,
        special_name=special_name,
        is_closed=is_closed,
        voter_show_names=show_short_names,
        voter_rows=rows,
    )


@bp.get("/<int:year>/voters")
@with_permissions
def year_voters(year: int, permissions: UserPermissions):
    return _render_year_voters(year, str(year), None, None, permissions)


@bp.get("/special/<short_name>/voters")
@with_permissions
def special_year_voters(short_name: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404
    return _render_year_voters(
        special_year["id"],
        short_name,
        short_name,
        special_year["special_name"],
        permissions,
    )

@bp.get("/<int:year>/<show>/voters")
@with_permissions
def show_voters(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if not permissions.can_view_restricted:
        return render_template("error.html", error="You aren't allowed to access this show"), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT username, nickname, COALESCE(country.id, 'XX') FROM vote_set
        JOIN account ON voter_id = account.id
        LEFT OUTER JOIN country ON country_id = country.id
        WHERE show_id = %s AND vote_set.result_mode = 'official'
    """,
        (show_data.id,),
    )

    return render_template("year/voters.html")


# ── Penalty management ───────────────────────────────────────────────
# Admins can dock a song its show's maximum point value when its
# submitter failed to vote in that show. The penalty is stored on
# ``song_show.penalty`` and the ``refresh_show_results`` SQL trigger
# automatically rebuilds the show's standings whenever it changes.
