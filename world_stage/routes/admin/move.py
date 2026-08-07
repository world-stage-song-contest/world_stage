
from flask import request

from ...db import get_db
from ...utils import (
    render_template,
    require_user,
)
from ...utils.entry_moves import EntryMoveError, move_entry
from .common import bp


@bp.get("/move")
def move():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id FROM year WHERE submissions_open AND id >= 0 ORDER BY id
    """)
    years = cursor.fetchall()

    cursor.execute("""
        SELECT id, name FROM country WHERE is_participating ORDER BY name
    """)
    countries = cursor.fetchall()

    return render_template("admin/move.html", years=years, countries=countries)


@bp.post("/move")
@require_user()
def move_post(user: tuple[int, str]):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id FROM year WHERE submissions_open AND id >= 0 ORDER BY id
    """)
    years = cursor.fetchall()

    cursor.execute("""
        SELECT id, name FROM country WHERE is_participating ORDER BY name
    """)
    countries = cursor.fetchall()

    from_year_txt = request.form.get("from_year")
    to_year_txt = request.form.get("to_year")

    from_cc = request.form.get("from_cc")
    to_cc = request.form.get("to_cc")

    if not from_year_txt or not from_cc:
        return render_template(
            "admin/move.html",
            error="From year and from country must be specificed",
            from_year=from_year_txt,
            to_year=to_year_txt,
            from_cc=from_cc,
            to_cc=to_cc,
            years=years,
            countries=countries,
        ), 400

    if not to_year_txt or not to_cc:
        return render_template(
            "admin/move.html",
            error="To year and to country must be specified",
            from_year=from_year_txt,
            to_year=to_year_txt,
            from_cc=from_cc,
            to_cc=to_cc,
            years=years,
            countries=countries,
        ), 400

    try:
        from_year = int(from_year_txt)
    except ValueError:
        return render_template(
            "admin/move.html",
            error="Invalid from year",
            from_year=from_year_txt,
            to_year=to_year_txt,
            from_cc=from_cc,
            to_cc=to_cc,
            years=years,
            countries=countries,
        ), 400

    try:
        to_year = int(to_year_txt)
    except ValueError:
        return render_template(
            "admin/move.html",
            error="Invalid to year",
            from_year=from_year_txt,
            to_year=to_year_txt,
            from_cc=from_cc,
            to_cc=to_cc,
            years=years,
            countries=countries,
        ), 400

    cursor.execute(
        """
        SELECT id FROM current_song
        WHERE year_id = %s AND country_id = %s
        ORDER BY entry_number
        """,
        (from_year, from_cc),
    )
    source = cursor.fetchone()
    if source is None:
        error = "Source entry not found"
    else:
        try:
            move_entry(
                cursor,
                source["id"],
                to_year=to_year,
                to_country=to_cc,
                changed_by=user[0],
            )
            error = None
        except EntryMoveError as exc:
            error = str(exc)
    if error:
        db.rollback()
        return render_template(
            "admin/move.html",
            error=error,
            from_year=from_year_txt,
            to_year=to_year_txt,
            from_cc=from_cc,
            to_cc=to_cc,
            years=years,
            countries=countries,
        ), 400
    db.commit()
    return render_template(
        "admin/move.html", message="Songs moved successfully.", years=years, countries=countries
    )
