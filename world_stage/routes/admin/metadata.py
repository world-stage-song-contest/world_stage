from typing import Any

import psycopg
from flask import redirect, request, url_for

from ...db import fetchone, get_db
from ...utils import (
    render_template,
)
from .common import bp


def _render_genre_index(error: str | None = None):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT genre.id AS genre_id, genre.name AS genre_name,
               subgenre.id AS subgenre_id, subgenre.name AS subgenre_name
        FROM genre
        LEFT JOIN subgenre ON subgenre.genre_id = genre.id
        ORDER BY genre.name COLLATE "C", subgenre.name COLLATE "C"
        """
    )
    grouped: dict[int, dict[str, Any]] = {}
    for r in cursor.fetchall():
        g_id = r["genre_id"]
        if g_id not in grouped:
            grouped[g_id] = {"id": g_id, "name": r["genre_name"], "subgenres": []}
        # Hide the auto-mirror subgenre that shares its name with the
        # parent genre — it exists for the song-tagging UX, not as a
        # distinct user-facing entry.
        if r["subgenre_id"] is not None and r["subgenre_name"] != r["genre_name"]:
            grouped[g_id]["subgenres"].append(
                {"id": r["subgenre_id"], "name": r["subgenre_name"]}
            )
    return render_template(
        "admin/genres.html", grouped=list(grouped.values()), error=error
    )


@bp.get("/genre")
def genre_index():
    return _render_genre_index()


@bp.post("/genre/<int:genre_id>/delete")
def genre_delete(genre_id: int):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT name FROM genre WHERE id = %s", (genre_id,))
    row = cur.fetchone()
    if not row:
        return redirect(url_for("admin.genre_index"))
    genre_name = row["name"]

    # Only allow deletion when the only remaining subgenre is the
    # auto-mirror (same name as the genre).
    cur.execute(
        "SELECT COUNT(*) AS c FROM subgenre WHERE genre_id = %s AND name <> %s",
        (genre_id, genre_name),
    )
    if fetchone(cur)["c"] > 0:
        return _render_genre_index(
            error=f"Cannot delete '{genre_name}' while it has subgenres. "
            "Delete its subgenres first."
        )

    cur.execute("DELETE FROM subgenre WHERE genre_id = %s", (genre_id,))
    cur.execute("DELETE FROM genre WHERE id = %s", (genre_id,))
    db.commit()
    return redirect(url_for("admin.genre_index"))


@bp.get("/language/create")
def language_create():
    cursor = get_db().cursor()
    cursor.execute("SELECT name FROM language ORDER BY name")
    return render_template(
        "admin/language_create.html",
        existing_languages=cursor.fetchall(),
    )


@bp.post("/language/create")
def language_create_post():
    def _clean(field: str) -> str | None:
        raw = (request.form.get(field) or "").strip()
        return raw or None

    name = (request.form.get("name") or "").strip()
    tag = (request.form.get("tag") or "").strip()
    extlang = _clean("extlang")
    region = _clean("region")
    subvariant = _clean("subvariant")
    suppress_script = _clean("suppress_script")
    code3 = _clean("code3")

    def _render_form(error: str):
        cursor = get_db().cursor()
        cursor.execute("SELECT name FROM language ORDER BY name")
        return render_template(
            "admin/language_create.html",
            existing_languages=cursor.fetchall(),
            error=error,
            name=name,
            tag=tag,
            extlang=extlang,
            region=region,
            subvariant=subvariant,
            suppress_script=suppress_script,
            code3=code3,
        )

    if not name:
        return _render_form("Name is required.")
    if not tag:
        return _render_form("Language tag is required.")

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO language
                (name, tag, extlang, region, subvariant, suppress_script, code3)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (name, tag, extlang, region, subvariant, suppress_script, code3),
        )
        db.commit()
    except psycopg.Error as e:
        db.rollback()
        return _render_form(str(e))

    return redirect(url_for("admin.language_create"))


def _render_alternative_name_form(error: str | None = None, **values):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, name FROM country ORDER BY name")
    countries = cursor.fetchall()
    cursor.execute("SELECT id FROM year ORDER BY id")
    years = cursor.fetchall()
    cursor.execute(
        """
        SELECT an.id, an.name, an.flag_variant, an.from_year_id, an.to_year_id,
               country.name AS country_name
        FROM alternative_name an
        JOIN country ON an.country_id = country.id
        ORDER BY country.name COLLATE "C", an.from_year_id NULLS FIRST
        """
    )
    existing = cursor.fetchall()
    return render_template(
        "admin/alternative_name.html",
        countries=countries,
        years=years,
        existing=existing,
        error=error,
        **values,
    )


@bp.get("/alternative-name")
def alternative_name_index():
    return _render_alternative_name_form()


@bp.post("/alternative-name")
def alternative_name_create_post():
    def _clean(field: str) -> str | None:
        raw = (request.form.get(field) or "").strip()
        return raw or None

    country_id = (request.form.get("country_id") or "").strip()
    name = _clean("name")
    flag_variant = _clean("flag_variant")
    from_year_txt = _clean("from_year")
    to_year_txt = _clean("to_year")

    values = dict(
        country_id=country_id,
        name=name,
        flag_variant=flag_variant,
        from_year=from_year_txt,
        to_year=to_year_txt,
    )

    if not country_id:
        return _render_alternative_name_form("Country is required.", **values)
    if not name and not flag_variant:
        return _render_alternative_name_form(
            "At least one of name or flag variant must be provided.", **values
        )

    try:
        from_year = int(from_year_txt) if from_year_txt else None
    except ValueError:
        return _render_alternative_name_form("Invalid from year.", **values)
    try:
        to_year = int(to_year_txt) if to_year_txt else None
    except ValueError:
        return _render_alternative_name_form("Invalid to year.", **values)

    if from_year is not None and to_year is not None and from_year > to_year:
        return _render_alternative_name_form(
            "From year must not be after to year.", **values
        )

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO alternative_name
                (country_id, from_year_id, to_year_id, name, flag_variant)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (country_id, from_year, to_year, name, flag_variant),
        )
        db.commit()
    except psycopg.Error as e:
        db.rollback()
        return _render_alternative_name_form(str(e), **values)

    return redirect(url_for("admin.alternative_name_index"))


@bp.post("/alternative-name/<int:name_id>/delete")
def alternative_name_delete(name_id: int):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM alternative_name WHERE id = %s", (name_id,))
        db.commit()
    except psycopg.Error as e:
        db.rollback()
        return _render_alternative_name_form(str(e))

    return redirect(url_for("admin.alternative_name_index"))


@bp.post("/subgenre/<int:subgenre_id>/delete")
def subgenre_delete(subgenre_id: int):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT subgenre.name AS sname, genre.name AS gname
        FROM subgenre
        JOIN genre ON subgenre.genre_id = genre.id
        WHERE subgenre.id = %s
        """,
        (subgenre_id,),
    )
    row = cur.fetchone()
    if not row:
        return redirect(url_for("admin.genre_index"))
    # Refuse to delete the auto-mirror directly; deleting the genre
    # cleans it up instead.
    if row["sname"] == row["gname"]:
        return _render_genre_index(
            error="Cannot delete the auto-mirror subgenre directly. "
            "Delete the parent genre instead."
        )

    cur.execute("DELETE FROM subgenre WHERE id = %s", (subgenre_id,))
    db.commit()
    return redirect(url_for("admin.genre_index"))


@bp.get("/genre/create")
def genre_create():
    cursor = get_db().cursor()
    cursor.execute('SELECT id, name FROM genre ORDER BY name COLLATE "C"')
    genres = cursor.fetchall()
    return render_template("admin/genre_create.html", genres=genres)


@bp.post("/genre/create")
def genre_create_post():
    genre_name = (request.form.get("genre_name") or "").strip()
    subgenre_name = (request.form.get("subgenre_name") or "").strip()

    def _render_form(error: str):
        cursor = get_db().cursor()
        cursor.execute('SELECT id, name FROM genre ORDER BY name COLLATE "C"')
        return render_template(
            "admin/genre_create.html",
            genres=cursor.fetchall(),
            error=error,
            genre_name=genre_name,
            subgenre_name=subgenre_name,
        )

    if not genre_name:
        return _render_form("Genre name is required.")

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("SELECT id FROM genre WHERE name = %s", (genre_name,))
        row = cur.fetchone()
        if row:
            genre_id = row["id"]
        else:
            cur.execute(
                "INSERT INTO genre (name) VALUES (%s) RETURNING id", (genre_name,)
            )
            genre_id = fetchone(cur)["id"]
            # Mirror the new genre as a subgenre under itself so songs
            # tagged only at the broad-category level still pick a row
            # from the subgenre table.
            cur.execute(
                "INSERT INTO subgenre (genre_id, name) VALUES (%s, %s)",
                (genre_id, genre_name),
            )

        if subgenre_name and subgenre_name != genre_name:
            cur.execute(
                """
                INSERT INTO subgenre (genre_id, name) VALUES (%s, %s)
                ON CONFLICT (genre_id, name) DO NOTHING
                """,
                (genre_id, subgenre_name),
            )

        db.commit()
    except psycopg.Error as e:
        db.rollback()
        return _render_form(str(e))

    return redirect(url_for("admin.genre_index"))
