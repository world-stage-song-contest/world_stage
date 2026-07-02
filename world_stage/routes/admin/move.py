
import psycopg
from flask import request

from ...db import get_db
from ...utils import (
    render_template,
)
from .common import bp


@bp.get("/move")
def move():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id FROM year ORDER BY id
    """)
    years = cursor.fetchall()

    cursor.execute("""
        SELECT id, name FROM country WHERE is_participating ORDER BY name
    """)
    countries = cursor.fetchall()

    return render_template("admin/move.html", years=years, countries=countries)


@bp.post("/move")
def move_post():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id FROM year ORDER BY id
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

    if not to_year_txt and not to_cc:
        return render_template(
            "admin/move.html",
            error="At least one of to year and to country must be specificed",
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
        to_year = int(to_year_txt) if to_year_txt else None
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

    try:
        cursor.execute(
            """
            UPDATE song
            SET year_id = COALESCE(%s, year_id),
                country_id = COALESCE(%s, country_id)
            WHERE year_id = %s AND country_id = %s
        """,
            (to_year, to_cc, from_year, from_cc),
        )
    except psycopg.Error as e:
        return render_template(
            "admin/move.html",
            error=f"Database error: {str(e)}",
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
