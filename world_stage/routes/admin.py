import contextlib
import csv
import datetime
import io
import json
import math
import os
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from flask import Blueprint, Response, current_app, redirect, request, url_for

from ..db import fetchone, get_db
from ..utils import (
    get_show_id,
    get_show_songs,
    get_user_role_from_session,
    get_year_shows,
    get_years,
    render_template,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def verify_user():
    session_id = request.cookies.get("session")
    if not session_id:
        return redirect("/")
    permissions = get_user_role_from_session(session_id)
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


@bp.get("/")
def index():
    resp = verify_user()
    if resp:
        return resp
    return render_template("admin/index.html")


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
    resp = verify_user()
    if resp:
        return resp
    return _render_genre_index()


@bp.post("/genre/<int:genre_id>/delete")
def genre_delete(genre_id: int):
    resp = verify_user()
    if resp:
        return resp

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
    resp = verify_user()
    if resp:
        return resp
    cursor = get_db().cursor()
    cursor.execute("SELECT name FROM language ORDER BY name")
    return render_template(
        "admin/language_create.html",
        existing_languages=cursor.fetchall(),
    )


@bp.post("/language/create")
def language_create_post():
    resp = verify_user()
    if resp:
        return resp

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


@bp.post("/subgenre/<int:subgenre_id>/delete")
def subgenre_delete(subgenre_id: int):
    resp = verify_user()
    if resp:
        return resp

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
    resp = verify_user()
    if resp:
        return resp

    cursor = get_db().cursor()
    cursor.execute('SELECT id, name FROM genre ORDER BY name COLLATE "C"')
    genres = cursor.fetchall()
    return render_template("admin/genre_create.html", genres=genres)


@bp.post("/genre/create")
def genre_create_post():
    resp = verify_user()
    if resp:
        return resp

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


@bp.get("/manage/<int:year>/createshow")
def create_show(year: int):
    resp = verify_user()
    if resp:
        return resp
    return render_template("admin/create_show.html", years=get_years(), year=year)


@bp.post("/manage/<int:year>/createshow")
def create_show_post(year: int):
    resp = verify_user()
    if resp:
        return resp

    data: dict[str, int | str | None] = {"year": year}
    value: int | str | None
    for key, value_ in request.form.items():
        value = value_
        with contextlib.suppress(ValueError):
            value = int(value)

        if not value:
            value = None

        data[key] = value

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
        """
        INSERT INTO show (year_id, point_system_id, show_name, short_name, dtf, sc, date, status)
        VALUES (%(year)s, 1, %(show_name)s, %(short_name)s, %(dtf)s, %(sc)s, %(date)s, 'none')
        """,
            data,
        )
        db.commit()
    except psycopg.Error as e:
        return render_template("admin/create_show.html", error=str(e))

    return redirect(url_for("admin.create_show", year=year))


def _render_draw(year_id: int, label: str):
    """Render the multi-show (semifinal) draw page for a given year. ``label``
    is shown in error messages and used as the JS RNG seed; for regular years
    that's just the numeric year, for specials it's the negative year id.

    Regular years group entries by ``country.pot`` (entries without a pot
    are excluded — same as the original behavior). Specials collapse every
    entry into a single combined pot.
    """
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id AS song_id, song.title, song.entry_number,
               song.submitter_id AS submitter,
               country.id AS cc, country.name, country.pot, country.genre,
               sl.language_id AS language
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT JOIN song_language sl
               ON sl.song_id = song.id AND sl.priority = 0
        WHERE song.year_id = %s AND NOT song.is_placeholder
        ORDER BY country.name, song.entry_number
        """,
        (year_id,),
    )
    entries = cursor.fetchall()

    single_pot = year_id < 0
    if single_pot:
        pots: dict[int, list[dict]] = {1: list(entries)}
    else:
        pots_raw: dict[int, list[dict]] = defaultdict(list)
        for entry in entries:
            pot = entry.get("pot")
            if pot is not None:
                pots_raw[pot].append(entry)
        pots = {k: pots_raw[k] for k in sorted(pots_raw.keys())}

    semifinalists = sum(len(p) for p in pots.values())

    shows = get_year_shows(year_id, pattern="sf")
    count = len(shows)
    if count == 0:
        return render_template(
            "error.html", error=f"No semifinal shows found for {label}"
        ), 404
    per = semifinalists // count
    songs = [per] * count
    deficit = semifinalists - per * count
    for i in range(deficit):
        songs[i] += 1

    limits = list(map(lambda n: math.ceil(n / 2), songs))

    return render_template(
        "admin/draw.html",
        pots=pots,
        shows=shows,
        songs=songs,
        limits=limits,
        year=year_id,
        single_pot=single_pot,
    )


@bp.get("/draw/<int:year>")
def draw(year: int):
    resp = verify_user()
    if resp:
        return resp
    return _render_draw(year, str(year))


@bp.get("/draw/special/<short_name>")
def draw_special(short_name: str):
    resp = verify_user()
    if resp:
        return resp

    special = _resolve_special(short_name)
    if not special:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return _render_draw(special["id"], special["special_name"] or short_name)


@bp.post("/draw/<int:year>")
def draw_post(year: int):
    resp = verify_user()
    if resp:
        return {"error": "Not an admin"}, 401

    # Each value is a list of song IDs in running order.
    data: dict[str, list[int]] | None = request.json
    if not data:
        return {"error": "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    try:
        for show, ro in data.items():
            show_data = get_show_id(show, year)
            if not show_data:
                return {"error": f"Invalid show {show} for {year}"}, 400

            for i, song_id in enumerate(ro):
                # Verify the song actually belongs to this year before
                # attaching it to a show — guards against bad client input.
                cursor.execute(
                    "SELECT id FROM song WHERE id = %s AND year_id = %s",
                    (song_id, year),
                )
                if not cursor.fetchone():
                    return {"error": f"Song {song_id} not found in year {year}"}, 400

                cursor.execute(
                    """
                    INSERT INTO song_show (song_id, show_id, running_order)
                    VALUES (%s, %s, %s)
                """,
                    (song_id, show_data.id, i + 1),
                )
    except psycopg.IntegrityError as e:
        print(e)
        return {"error": "Duplicate data"}, 400

    db.commit()
    return {}, 204


@bp.post("/draw/special/<short_name>")
def draw_special_post(short_name: str):
    special = _resolve_special(short_name)
    if not special:
        return {"error": f"Special '{short_name}' not found"}, 404
    return draw_post(special["id"])


@bp.get("/draw/special/<short_name>/<show>")
def draw_special_final(short_name: str, show: str):
    resp = verify_user()
    if resp:
        return resp

    special = _resolve_special(short_name)
    if not special:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return draw_final(special["id"], show)


@bp.post("/draw/special/<short_name>/<show>")
def draw_special_final_post(short_name: str, show: str):
    special = _resolve_special(short_name)
    if not special:
        return {"error": f"Special '{short_name}' not found"}, 404
    return draw_final_post(special["id"], show)


@bp.get("/draw/<int:year>/<show>")
def draw_final(year: int, show: str):
    resp = verify_user()
    if resp:
        return resp

    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error=f"Invalid show '{show}' for {year}"), 404

    songs = get_show_songs(year, show, sort_reveal=True)

    if not songs:
        return render_template("error.html", error="No show '{show}' found for {year}"), 404

    # Genre is set per-country (not per-song) and isn't on the Country
    # dataclass, so look it up separately and pass it as a {cc → genre}
    # mapping to the template. Same idea for the per-song primary
    # language ({song_id → language_id}).
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, genre FROM country WHERE id = ANY(%s)",
        ([s.country.cc for s in songs],),
    )
    genre_by_cc = {row["id"]: row["genre"] for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT song_id, language_id FROM song_language
        WHERE song_id = ANY(%s) AND priority = 0
        """,
        ([s.id for s in songs],),
    )
    language_by_song = {row["song_id"]: row["language_id"] for row in cursor.fetchall()}

    return render_template(
        "admin/draw_individual.html",
        songs=songs,
        genre_by_cc=genre_by_cc,
        language_by_song=language_by_song,
        show=show,
        show_name=show_data.name,
        year=year,
        num=len(songs),
        lim=math.ceil((len(songs) / 2) or 1),
    )


@bp.post("/draw/<int:year>/<show>")
def draw_final_post(year: int, show: str):
    resp = verify_user()
    if resp:
        return {"error": "Not an admin"}, 401

    # The client now sends a list of song IDs (in running order).
    data: dict[str, list[int]] | None = request.json
    if not data:
        return {"error": "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    show_data = get_show_id(show, year)
    if not show_data:
        return {"error": f"Invalid show '{show}' for {year}"}, 400

    ro = data.get(show)
    if ro is None:
        return {"error": "No running order provided"}, 400

    for i, song_id in enumerate(ro):
        cursor.execute(
            "SELECT id FROM song WHERE id = %s AND year_id = %s",
            (song_id, year),
        )
        if not cursor.fetchone():
            return {"error": f"Song {song_id} not found in year {year}"}, 400

        cursor.execute(
            """
            UPDATE song_show
            SET running_order = %s
            WHERE song_id = %s AND show_id = %s
        """,
            (i + 1, song_id, show_data.id),
        )

    db.commit()
    return {}, 204


ALL_EVENT_TYPES = [
    "create",
    "delete",
    "song_replacement",
    "song_modification",
    "placeholder_on",
    "placeholder_off",
    "ownership_change",
]


@bp.get("/changes")
def changes():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    per_page = 250

    # Event type filtering
    selected_events = request.args.getlist("events")
    if not selected_events:
        selected_events = list(ALL_EVENT_TYPES)
    # Ensure only valid event types
    selected_events = [e for e in selected_events if e in ALL_EVENT_TYPES]
    if not selected_events:
        selected_events = list(ALL_EVENT_TYPES)

    # Count total matching entries
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM song_audit_log WHERE event_type = ANY(%s)", (selected_events,)
    )
    total = fetchone(cursor)["cnt"]
    total_pages = max(1, math.ceil(total / per_page))

    page = request.args.get("page", 1, type=int)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    cursor.execute(
        """
        SELECT
            sal.id,
            sal.event_type,
            sal.changed_at,
            sal.song_id,
            sal.song_title,
            sal.song_artist,
            sal.song_country_id,
            sal.song_year_id,
            sal.changed_fields,
            a.username  AS changed_by_username,
            c.name      AS country_name
        FROM song_audit_log sal
        LEFT JOIN account a ON a.id = sal.changed_by
        LEFT JOIN country c ON c.id = sal.song_country_id
        WHERE sal.event_type = ANY(%s)
        ORDER BY sal.changed_at DESC
        LIMIT %s OFFSET %s
    """,
        (selected_events, per_page, offset),
    )
    changes = cursor.fetchall()

    # Resolve submitter IDs to usernames for ownership_change entries.
    submitter_ids: set[int] = set()
    for entry in changes:
        if entry["event_type"] == "ownership_change" and entry["changed_fields"]:
            cf = entry["changed_fields"]
            if "submitter_id" in cf:
                for key in ("old", "new"):
                    val = cf["submitter_id"].get(key)
                    if val is not None:
                        submitter_ids.add(int(val))
    username_map: dict = {}
    if submitter_ids:
        cursor.execute(
            "SELECT id, username FROM account WHERE id = ANY(%s)", (list(submitter_ids),)
        )
        for row in cursor.fetchall():
            username_map[row["id"]] = row["username"]
            username_map[str(row["id"])] = row["username"]

    return render_template(
        "admin/changes.html",
        changes=changes,
        username_map=username_map,
        page=page,
        total_pages=total_pages,
        total=total,
        all_event_types=ALL_EVENT_TYPES,
        selected_events=selected_events,
    )


@bp.get("/move")
def move():
    resp = verify_user()
    if resp:
        return resp

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
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

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


@bp.get("/manage")
def manage_index():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id, status, special_name, special_short_name
        FROM year ORDER BY id
        """
    )
    rows = cursor.fetchall()
    regular_years = [r for r in rows if r["id"] >= 0]
    specials = [r for r in rows if r["id"] < 0]

    return render_template(
        "admin/manage_index.html", years=regular_years, specials=specials
    )


def _render_manage(year_id: int, year_data: dict):
    """Render the show-management page for a given year row."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show_name, short_name, date, status, voting_opens, voting_closes, predictions_close
        FROM show WHERE year_id = %s
        ORDER BY id
    """,
        (year_id,),
    )
    shows = cursor.fetchall()
    return render_template("admin/manage_shows.html", year=year_data, shows=shows)


@bp.get("/manage/<int:year>")
def manage(year: int):
    resp = verify_user()
    if resp:
        return resp

    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, status FROM year WHERE id = %s AND id >= 0",
        (year,),
    )
    year_data = cursor.fetchone()
    if not year_data:
        return render_template("error.html", error=f"Year {year} not found"), 404

    return _render_manage(year, year_data)


@bp.get("/manage/special/<short_name>")
def manage_special(short_name: str):
    resp = verify_user()
    if resp:
        return resp

    year_data = _resolve_special(short_name)
    if not year_data:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return _render_manage(year_data["id"], year_data)


@bp.post("/manage/<int:year>")
def manage_post(year: int):
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    body = request.get_json()
    if not body:
        return render_template("error.html", error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    action = body.get("action")
    if not action:
        return render_template("error.html", error="No action specified"), 400

    match action:
        case "change_year_status":
            status = body.get("year_status")
            if status not in ("open", "closed", "ongoing"):
                return render_template("error.html", error="Invalid year status"), 400

            cursor.execute(
                """
                UPDATE year
                SET status = %s
                WHERE id = %s
            """,
                (status, year),
            )
        case _:
            return render_template("error.html", error=f"Unknown action '{action}'"), 400
    db.commit()
    return {"status": "success"}, 200


@bp.post("/manage/special/<short_name>")
def manage_special_post(short_name: str):
    year = _resolve_special(short_name)
    if not year:
        return {"error": f"Special '{short_name}' not found"}, 404
    return manage_post(year["id"])


@bp.post("/manage/special/<short_name>/<show>")
def manage_show_special_post(short_name: str, show: str):
    year = _resolve_special(short_name)
    if not year:
        return {"error": f"Special '{short_name}' not found"}, 404
    return manage_show_post(year["id"], show)


@bp.post("/manage/<int:year>/<show>")
def manage_show_post(year: int, show: str):
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    body = request.get_json()
    if not body:
        return render_template("error.html", error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    action = body.get("action")
    if not action:
        return render_template("error.html", error="No action specified"), 400

    match action:
        case "open_voting":
            cursor.execute(
                """
                UPDATE show
                SET voting_opens = COALESCE(voting_opens, CURRENT_TIMESTAMP)
                  , voting_closes = NULL
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "close_voting":
            cursor.execute(
                """
                UPDATE show
                SET voting_closes = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "close_predictions":
            cursor.execute(
                """
                UPDATE show
                SET predictions_close = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "open_predictions":
            cursor.execute(
                """
                UPDATE show
                SET predictions_close = NULL
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "set_status":
            status = body.get("status")
            if status not in ("none", "draw", "partial", "full"):
                return render_template("error.html", error="Invalid show status"), 400

            cursor.execute(
                """
                UPDATE show
                SET status = %s
                WHERE year_id = %s AND short_name = %s
            """,
                (status, year, show),
            )
        case "change_date":
            date_str = body.get("date")
            if not date_str:
                return render_template("error.html", error="No date provided"), 400
            try:
                date = datetime.date.fromisoformat(date_str)
            except ValueError:
                return render_template("error.html", error="Invalid date format"), 400
            if not date:
                return render_template("error.html", error="Invalid date format"), 400

            cursor.execute(
                """
                UPDATE show
                SET date = %s
                WHERE year_id = %s AND short_name = %s
            """,
                (date, year, show),
            )
        case _:
            return render_template("error.html", error=f"Unknown action '{action}'"), 400

    db.commit()

    return {"status": "success"}, 200


@bp.get("/fuckupdb")
def fuckup_db():
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    return render_template("admin/fuckupdb.html")


@bp.post("/fuckupdb")
def fuckup_db_post():
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    db = get_db()
    cursor = db.cursor()

    query = request.form.get("query")
    if not query:
        return render_template("admin/fuckupdb.html", error="No query provided"), 400

    subprocess.run(current_app.config.get("BACKUP_SCRIPT", os.environ.get("BACKUP_SCRIPT", "")))

    try:
        cursor.execute("SET ROLE dml_only_role")
        cursor.execute(query)  # type: ignore
        db.commit()

        rows = []
        headers = []
        if cursor.description is not None:
            rows = cursor.fetchall()
            headers = (
                [description[0] for description in cursor.description] if cursor.description else []
            )

        cursor.execute("RESET ROLE")
    except psycopg.Error as e:
        db.rollback()
        return render_template(
            "admin/fuckupdb.html", error=f"Query failed: {str(e)}", query=query
        ), 400

    kind = request.form.get("kind")
    if kind == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

        filename = datetime.datetime.now(tz=datetime.UTC).strftime("query_%Y%m%dT%H%M%SZ.csv")

        response = Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
        return response
    elif kind == "html":
        return render_template("admin/fuckupdb.html", rows=rows, headers=headers, query=query)
    else:
        return render_template("admin/fuckupdb.html", error=f"Unknown filetype: {kind}"), 400


@bp.get("/users")
def users():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id, username, approved, role
        FROM account
        ORDER BY id
    """)
    users = cursor.fetchall()

    return render_template("admin/users.html", users=users)


@bp.post("/users")
def users_post():
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    body = request.get_json()
    if not body:
        return render_template("error.html", error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    user_id = body.get("user_id")
    action = body.get("action")

    if not user_id or not action:
        return render_template("error.html", error="User ID and action must be provided"), 400

    if action == "approve":
        cursor.execute(
            """
            UPDATE account
            SET approved = true
            WHERE id = %s
        """,
            (user_id,),
        )
    elif action == "unapprove":
        cursor.execute(
            """
            UPDATE account
            SET approved = false
            WHERE id = %s
        """,
            (user_id,),
        )
    elif action == "annul_password":
        cursor.execute(
            """
            UPDATE account
            SET password = NULL, salt = NULL
            WHERE id = %s
        """,
            (user_id,),
        )
    else:
        return render_template("error.html", error=f"Unknown action '{action}'"), 400

    db.commit()

    return {"status": "success"}, 200


@bp.get("/manage/<int:year>/setpots")
def set_pots(year: int):
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT country.id, name, pot, genre FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON song.year_id = year.id
        WHERE year_id = %s AND year.host_id IS DISTINCT FROM country.id
        ORDER BY pot, name
    """,
        (year,),
    )
    countries = cursor.fetchall()

    return render_template("admin/set_pots.html", countries=countries, year=year)


@bp.post("/manage/<int:year>/setpots")
def set_pots_post(year: int):
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    db = get_db()
    cursor = db.cursor()

    # Form fields are name-prefixed: ``pot_<country_id>`` and
    # ``genre_<country_id>``. Both follow the same "0 → NULL" convention.
    updates: dict[str, dict[str, int | None]] = {}
    for key, value in request.form.items():
        if key.startswith("pot_"):
            field, country_id = "pot", key[len("pot_"):]
        elif key.startswith("genre_"):
            field, country_id = "genre", key[len("genre_"):]
        else:
            continue
        try:
            parsed: int | None = int(value)
            if parsed == 0:
                parsed = None
        except ValueError:
            return render_template(
                "error.html",
                error=f"Invalid {field} value for country {country_id}",
            ), 400
        updates.setdefault(country_id, {})[field] = parsed

    # Clear all pots/genres first so countries no longer in the form
    # (e.g. removed from this year) are reset.
    cursor.execute("UPDATE country SET pot = NULL, genre = NULL")

    for country_id, fields in updates.items():
        cursor.execute(
            """
            UPDATE country
            SET pot = %s, genre = %s
            WHERE id = %s
        """,
            (fields.get("pot"), fields.get("genre"), country_id),
        )

    db.commit()
    return redirect(url_for("admin.set_pots", year=year))


@bp.post("/manage/<int:year>/setpots/json")
def set_pots_json(year: int):
    """Bulk-update pots/genres from a single JSON payload of the form
    ``{"US": {"pot": 1, "genre": 1}, "RU": {"pot": 2, "genre": 3}, ...}``.

    Same conventions as the form-encoded endpoint: a value of 0 (or a
    missing key) maps to NULL, and any country not listed in the payload
    has its pot and genre cleared.
    """
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    # The payload may arrive as raw JSON in the request body or as a
    # ``payload`` form field (used by the textarea on the page).
    raw_payload: dict | None = None
    if request.is_json:
        raw_payload = request.get_json(silent=True)
    else:
        text = request.form.get("payload", "").strip()
        if text:
            try:
                raw_payload = json.loads(text)
            except json.JSONDecodeError as e:
                return render_template("error.html", error=f"Invalid JSON: {e}"), 400

    if raw_payload is None:
        return render_template("error.html", error="No JSON payload provided"), 400
    if not isinstance(raw_payload, dict):
        return render_template(
            "error.html", error="Top-level JSON value must be an object"
        ), 400

    def _coerce(label: str, country_id: str, value):
        """Apply the same '0 / null → NULL' convention as the form path."""
        if value is None:
            return None
        try:
            n = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid {label} for country {country_id}: {value!r}")
        return None if n == 0 else n

    # Validate everything before touching the database so a bad payload
    # doesn't half-apply.
    parsed: dict[str, tuple[int | None, int | None]] = {}
    for country_id, fields in raw_payload.items():
        if not isinstance(fields, dict):
            return render_template(
                "error.html",
                error=f"Expected object for country {country_id}",
            ), 400
        try:
            pot = _coerce("pot", country_id, fields.get("pot"))
            genre = _coerce("genre", country_id, fields.get("genre"))
        except ValueError as e:
            return render_template("error.html", error=str(e)), 400
        parsed[country_id] = (pot, genre)

    db = get_db()
    cursor = db.cursor()

    cursor.execute("UPDATE country SET pot = NULL, genre = NULL")
    for country_id, (pot, genre) in parsed.items():
        cursor.execute(
            "UPDATE country SET pot = %s, genre = %s WHERE id = %s",
            (pot, genre, country_id),
        )

    db.commit()
    return redirect(url_for("admin.set_pots", year=year))


@bp.get("/upload")
def upload():
    resp = verify_user()
    if resp:
        return resp

    return render_template("admin/upload.html")


@bp.post("/upload")
def upload_post():
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    file = request.files.get("file")
    if not file:
        return render_template("error.html", error="No file uploaded"), 400

    file_path = Path(
        current_app.instance_path,
        "uploads",
        file.filename or datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".dat",
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file.save(file_path)

    return render_template(
        "admin/upload.html",
        message=f"File '{file.filename}' uploaded successfully.",
        file_path=str(file_path),
    )


@bp.get("/predictions")
def predictions_index():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT show.id, show.show_name, show.short_name, show.year_id AS year,
               COUNT(prediction_set.id) AS prediction_count
        FROM show
        LEFT JOIN prediction_set ON prediction_set.show_id = show.id
        GROUP BY show.id, show.show_name, show.short_name, show.year_id
        ORDER BY show.id
    """)
    shows = cursor.fetchall()

    return render_template("admin/predictions_index.html", shows=shows)


@bp.get("/recapdata")
def recap_data():
    resp = verify_user()
    if resp:
        return resp

    return render_template("admin/recap_data.html")


class BadRecapRequestError(ValueError):
    pass


def _require(condition: bool, msg: str) -> None:
    if not condition:
        raise BadRecapRequestError(msg)


def _parse_year(s: str) -> int:
    _require(
        len(s) == 4 and s.isdigit(), f"Year '{s}' is invalid. It needs to have exactly four digits."
    )
    return int(s)


def _parse_cc2(s: str) -> str:
    _require(len(s) == 2, f"Code '{s}' is invalid. It needs to have exactly two characters.")
    return s


def _parse_show_key(s: str) -> tuple[int, str]:
    try:
        year_s, short_name = s.split("-", 1)
    except ValueError as err:
        raise BadRecapRequestError(f"Invalid number of dashes in the value '{s}'") from err

    year = _parse_year(year_s)
    _require(bool(short_name), f"Invalid show name '{short_name}' in show '{s}'")
    return year, short_name


def _lookup_many(
    cursor,
    sql: LiteralString,
    params_list: Sequence[tuple[Any, ...]],
    *,
    not_found_msg: Callable[[tuple[Any, ...]], str],
) -> list[int]:
    out: list[int] = []
    for params in params_list:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if not row:
            raise BadRecapRequestError(not_found_msg(params))
        out.append(row["id"])
    return out


@dataclass(frozen=True)
class Spec:
    parse: Callable[[list[str], Any], Any]  # (form_data, cursor) -> param
    sql: LiteralString


def _parse_show_ids(form_data: list[str], cursor) -> list[int]:
    keys = [_parse_show_key(s) for s in form_data]
    return _lookup_many(
        cursor,
        "SELECT id FROM show WHERE year_id = %s AND short_name = %s",
        keys,
        not_found_msg=lambda p: f"Show '{p[0]}-{p[1]}' not found",
    )


def _parse_years(form_data: list[str], cursor) -> list[int]:
    return [_parse_year(s) for s in form_data]


def _parse_countries(form_data: list[str], cursor) -> list[str]:
    return [_parse_cc2(s) for s in form_data]


def _parse_submitter_ids(form_data: list[str], cursor) -> list[int]:
    params_list = [(u,) for u in form_data]
    return _lookup_many(
        cursor,
        "SELECT id FROM account WHERE username = %s",
        params_list,
        not_found_msg=lambda p: f"User '{p[0]}' is unknown",
    )


_SQL_SHOW = """
WITH song_data AS (
    SELECT DISTINCT ON (song.id, show.id)
           show.id as show_id, show.year_id || short_name AS show, running_order AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song_show
    JOIN song ON song_show.song_id = song.id
    JOIN show ON song_show.show_id = show.id
    JOIN country ON song.country_id = country.id
    WHERE show.id = ANY(%s)
    ORDER BY song.id, show.id
)
SELECT show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY show_id, ro
"""

_SQL_YEAR = """
WITH song_data AS (
    SELECT song.year_id as show_id, song.year_id AS show, UPPER(country.id) AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    WHERE song.year_id = ANY(%s)
    ORDER BY LOWER(country.id)
)
SELECT show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY country
"""

_SQL_COUNTRY = """
WITH song_data AS (
    SELECT song.year_id AS year, LOWER(country.id) as show_id, LOWER(country.id) AS show,
           MOD(song.year_id, 100) AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    JOIN year ON song.year_id = year.id
    WHERE country.id = ANY(%s) AND year_id IS NOT NULL AND year.status = 'closed'
    ORDER BY song.year_id
)
SELECT show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY year
"""

_SQL_SUBMITTER = """
WITH song_data AS (
    SELECT account.username as show_id, song.year_id AS year, account.username AS show,
           MOD(song.year_id, 100) AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    JOIN year ON song.year_id = year.id
    JOIN account ON song.submitter_id = account.id
    WHERE song.submitter_id = ANY(%s) AND year_id IS NOT NULL AND year.status = 'closed'
    ORDER BY song.year_id, country
)
SELECT year, show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY year, country
"""

_SPECS: dict[str, Spec] = {
    "show": Spec(parse=_parse_show_ids, sql=_SQL_SHOW),
    "year": Spec(parse=_parse_years, sql=_SQL_YEAR),
    "country": Spec(parse=_parse_countries, sql=_SQL_COUNTRY),
    "submitter": Spec(parse=_parse_submitter_ids, sql=_SQL_SUBMITTER),
}


def get_recap_data(mode: str, form_data: list[str]) -> list[dict] | None:
    db = get_db()
    cursor = db.cursor()

    spec = _SPECS.get(mode)
    if not spec:
        return None

    try:
        param = spec.parse(form_data, cursor)
        cursor.execute(spec.sql, (param,))
        return cursor.fetchall()
    except BadRecapRequestError:
        return None


def drop_none(obj):
    if isinstance(obj, dict):
        return {k: drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [drop_none(v) for v in obj]
    return obj


@bp.post("/recapdata")
def recap_data_post() -> Response | tuple[Response, int]:
    resp = verify_user()
    if resp:
        return render_template("error.html", error="Not an admin"), 401

    form_data = request.form.getlist("show")
    type = request.form.get("type", "")
    action = request.form.get("action", "render")
    pretty = request.form.get("pretty", "off") == "on"
    indent = 2 if pretty else None

    data = get_recap_data(type, form_data)
    if data is None:
        return render_template("error.html", error="An error has occured")

    json_data = json.dumps(drop_none(data), ensure_ascii=False, indent=indent)

    if action == "render":
        return render_template("admin/recap_data.html", data=json_data)
    elif action == "download":
        response = Response(
            json_data,
            mimetype="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={'+'.join(form_data)}.json",
            },
        )
        return response
    else:
        return render_template("error.html", error=f"Unknown action: {action}")
