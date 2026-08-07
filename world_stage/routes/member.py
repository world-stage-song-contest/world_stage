from typing import Any

from flask import Blueprint, redirect, request, url_for

from ..db import fetchone, get_db
from ..messaging import has_unread_messages
from ..utils import (
    UserPermissions,
    format_seconds,
    get_user_id_from_session,
    get_user_permissions,
    get_years_grouped,
    render_template,
    require_user,
    resolve_country_code,
    with_permissions,
)
from ..utils.entry_moves import EntryMoveError, move_entry
from ..utils.song_revisions import MAX_YEAR_SUBMISSIONS

bp = Blueprint("member", __name__, url_prefix="/member")

MAX_USER_SUBMISSIONS = 2


def get_languages() -> list[dict]:
    """Get all available languages"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, name FROM language ORDER BY name")
    return cursor.fetchall()


def get_genre_options() -> list[dict]:
    """Return all subgenres grouped by parent genre, ordered for the
    submit form's optgroup dropdowns. Within each genre, the auto-mirror
    subgenre (the one whose name matches the genre) sorts first; the
    remaining subgenres follow alphabetically.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT genre.id AS genre_id, genre.name AS genre_name,
               subgenre.id AS subgenre_id, subgenre.name AS subgenre_name
        FROM genre
        JOIN subgenre ON subgenre.genre_id = genre.id
        ORDER BY genre.name,
                 (subgenre.name = genre.name) DESC,
                 subgenre.name
        """
    )
    grouped: dict[int, dict] = {}
    for r in cursor.fetchall():
        gid = r["genre_id"]
        if gid not in grouped:
            grouped[gid] = {
                "id": gid,
                "name": r["genre_name"],
                "subgenres": [],
            }
        grouped[gid]["subgenres"].append({"id": r["subgenre_id"], "name": r["subgenre_name"]})
    return list(grouped.values())


def get_countries(year: int, user_id: int | None, all: bool = False) -> dict[str, Any]:
    """Get countries available for submission"""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT submissions_open FROM year WHERE id = %s", (year,))
    year_result = cursor.fetchone()
    if not year_result:
        return {"own": [], "placeholder": [], "force_placeholder": False}

    submissions_closed = not year_result["submissions_open"]
    is_special = year < 0
    user_limit = 1 if is_special else MAX_USER_SUBMISSIONS

    cursor.execute(
        """SELECT COUNT(*) AS c FROM current_song AS song
           WHERE year_id = %s AND main_participant AND NOT is_placeholder""",
        (year,),
    )
    year_count = fetchone(cursor)["c"]

    cursor.execute(
        """
        SELECT COUNT(*) AS c FROM current_song AS song
        WHERE submitter_id = %s AND year_id = %s
          AND main_participant AND NOT is_placeholder
    """,
        (user_id, year),
    )
    user_count = fetchone(cursor)["c"]

    force_placeholder = (
        not all
        and not is_special
        and not submissions_closed
        and (user_count >= MAX_USER_SUBMISSIONS or year_count >= MAX_YEAR_SUBMISSIONS)
    )
    countries: dict[str, Any] = {
        "own": [],
        "placeholder": [],
        "force_placeholder": force_placeholder,
    }

    # Get user's own submissions
    cursor.execute(
        """
        SELECT country.name, country.id AS cc FROM current_song AS song
        JOIN country ON song.country_id = country.id
        WHERE song.year_id = %s AND song.submitter_id = %s
          AND song.main_participant
          AND NOT EXISTS (
              SELECT 1 FROM national_final
              WHERE national_final.year_id = song.year_id
                AND national_final.owner_country_id = song.country_id
                AND national_final.status <> 'cancelled'
          )
        ORDER BY country.name
    """,
        (year, user_id),
    )
    countries["own"] = cursor.fetchall()

    # Availability filter: specials ignore year-range checks since all
    # participating countries are always eligible.
    availability_filter = (
        "is_participating"
        if is_special
        else "available_from <= %(year)s AND available_until >= %(year)s AND is_participating"
    )

    if all:
        if submissions_closed and not is_special:
            cursor.execute(
                """
                SELECT country.name, country.id AS cc FROM current_song AS song
                JOIN country ON song.country_id = country.id
                WHERE song.year_id = %(year)s
                  AND submitter_id IS DISTINCT FROM %(user)s
                  AND NOT EXISTS (
                      SELECT 1 FROM national_final
                      WHERE national_final.year_id = song.year_id
                        AND national_final.owner_country_id = song.country_id
                        AND national_final.status <> 'cancelled'
                  )
                ORDER BY country.name
            """,
                {"year": year, "user": user_id},
            )
        else:
            cursor.execute(
                f"""
                SELECT name, id AS cc FROM country
                WHERE {availability_filter}
                      AND NOT EXISTS (
                          SELECT 1 FROM national_final
                          WHERE national_final.year_id = %(year)s
                            AND national_final.owner_country_id = country.id
                            AND national_final.status <> 'cancelled'
                      )
                      AND id NOT IN (
                          SELECT country_id FROM current_song AS song
                          WHERE year_id = %(year)s AND submitter_id = %(user)s
                            AND main_participant
                      )
                ORDER BY name
            """,
                {"year": year, "user": user_id},
            )
        countries["placeholder"] = cursor.fetchall()
    elif is_special and user_count < user_limit:
        # Specials: one submission per user. A country can have multiple
        # entries across users, so only exclude countries the current
        # user already claimed.
        cursor.execute(
            f"""
            SELECT c.name, c.id AS cc
            FROM country AS c
            WHERE {availability_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM national_final
                  WHERE national_final.year_id = %(year)s
                    AND national_final.owner_country_id = c.id
                    AND national_final.status <> 'cancelled'
              )
              AND NOT EXISTS (
                SELECT 1 FROM current_song AS s
                WHERE s.year_id = %(year)s AND s.country_id = c.id
                  AND s.submitter_id = %(user)s AND s.main_participant
              )
            ORDER BY c.name
        """,
            {"year": year, "user": user_id},
        )
        countries["placeholder"] = cursor.fetchall()
    elif not is_special and not submissions_closed:
        cursor.execute(
            f"""
            SELECT c.name, c.id AS cc
            FROM country AS c
            WHERE {availability_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM national_final
                  WHERE national_final.year_id = %(year)s
                    AND national_final.owner_country_id = c.id
                    AND national_final.status <> 'cancelled'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM current_song AS s
                WHERE s.year_id = %(year)s
                AND s.country_id = c.id
                AND s.main_participant AND s.is_placeholder = FALSE
                AND s.submitter_id <> %(user)s
            )
            ORDER BY c.name
        """,
            {"year": year, "user": user_id},
        )
        countries["placeholder"] = cursor.fetchall()

    return countries


def get_users() -> list[dict]:
    """Get all users for admin dropdown"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, username FROM account ORDER BY username")
    return cursor.fetchall()


# Route handlers
@bp.get("/")
@require_user(redirect_to_login=True)
@with_permissions
def index(user: tuple[int, str], permissions: UserPermissions):
    cursor = get_db().cursor()
    cursor.execute("SELECT COUNT(*) AS count FROM national_final WHERE owner_id = %s", (user[0],))
    return render_template(
        "member/index.html",
        username=user[1],
        has_unread_messages=has_unread_messages(user[0], permissions),
        owns_national_finals=cursor.fetchone()["count"] > 0,
    )


def _move_page(*, error=None):
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id FROM year WHERE submissions_open AND id >= 0 ORDER BY id"
    )
    years = cursor.fetchall()
    return render_template(
        "member/move.html",
        years=years,
        error=error,
    )


@bp.get("/move")
@require_user(redirect_to_login=True)
def move(user: tuple[int, str]):
    return _move_page()


@bp.get("/move/<int:year>")
@require_user()
def move_entries(year: int, user: tuple[int, str]):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id, song.country_id AS cc, country.name AS country
        FROM current_song AS song
        JOIN country ON country.id = song.country_id
        JOIN year ON year.id = song.year_id
        WHERE song.submitter_id = %s AND song.year_id = %s
          AND year.submissions_open AND year.id >= 0
        ORDER BY country.name, song.entry_number
        """,
        (user[0], year),
    )
    return {"entries": cursor.fetchall()}


@bp.get("/move/destinations/<int:year>")
@require_user()
def move_destinations(year: int, user: tuple[int, str]):
    try:
        song_id = int(request.args.get("song_id", ""))
    except ValueError:
        return {"error": "Invalid entry"}, 400

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id, song.year_id, song.country_id
        FROM current_song AS song
        JOIN year ON year.id = song.year_id
        WHERE song.id = %s AND song.submitter_id = %s
          AND year.submissions_open AND year.id >= 0
        """,
        (song_id, user[0]),
    )
    source = cursor.fetchone()
    if source is None:
        return {"error": "Entry not found"}, 404

    cursor.execute(
        "SELECT submissions_open FROM year WHERE id = %s AND id >= 0", (year,)
    )
    destination_year = cursor.fetchone()
    if destination_year is None or not destination_year["submissions_open"]:
        return {"error": "Destination must be open for submissions"}, 400

    cursor.execute(
        """
        SELECT country.id AS cc, country.name,
               EXISTS (
                   SELECT 1 FROM current_song AS placeholder
                   WHERE placeholder.year_id = %(year)s
                     AND placeholder.country_id = country.id
                     AND placeholder.is_placeholder
               ) AS replaces_placeholder
        FROM country
        WHERE country.is_participating
          AND NOT EXISTS (
              SELECT 1 FROM national_final
              WHERE national_final.year_id = %(year)s
                AND national_final.owner_country_id = country.id
                AND national_final.status <> 'cancelled'
          )
          AND NOT (
              %(year)s = %(source_year)s
              AND country.id = %(source_country)s
          )
          AND NOT EXISTS (
              SELECT 1 FROM current_song AS occupied
              WHERE occupied.year_id = %(year)s
                AND occupied.country_id = country.id
                AND NOT occupied.is_placeholder
          )
        ORDER BY country.name
        """,
        {
            "year": year,
            "source_year": source["year_id"],
            "source_country": source["country_id"],
        },
    )
    return {"countries": cursor.fetchall()}


@bp.post("/move")
@require_user(redirect_to_login=True)
def move_post(user: tuple[int, str]):
    payload = request.get_json(silent=True) or request.form
    try:
        song_id = int(payload.get("song_id", ""))
        to_year = int(payload.get("to_year", ""))
    except (TypeError, ValueError):
        if request.accept_mimetypes.accept_json:
            return {"error": {"description": "Invalid entry or year"}}, 400
        return _move_page(error="Invalid entry or year"), 400
    to_country = payload.get("to_country", "")
    if not to_country:
        if request.accept_mimetypes.accept_json:
            return {"error": {"description": "Destination country is required"}}, 400
        return _move_page(error="Destination country is required"), 400

    db = get_db()
    try:
        move_entry(
            db.cursor(),
            song_id,
            to_year=to_year,
            to_country=to_country,
            changed_by=user[0],
            submitter_id=user[0],
        )
    except EntryMoveError as exc:
        db.rollback()
        if request.accept_mimetypes.accept_json:
            return {"error": {"description": str(exc)}}, 400
        return _move_page(error=str(exc)), 400
    db.commit()
    details_url = url_for("country.details", code=to_country.lower(), year=to_year)
    if request.accept_mimetypes.accept_json:
        return {
            "result": {
                "id": song_id,
                "year": to_year,
                "country_id": to_country,
                "details_url": details_url,
            }
        }
    return redirect(details_url)


@bp.get("/submit")
@require_user(redirect_to_login=True)
@with_permissions
def submit(user: tuple[int, str], permissions: UserPermissions):
    year = request.args.get("year")
    country = request.args.get("country")
    entry_number = request.args.get("entry_number")
    national_final_id = request.args.get("national_final_id", type=int)
    national_final = None
    countries = {}
    if national_final_id is not None:
        cursor = get_db().cursor()
        cursor.execute(
            """
            SELECT national_final.*, year.special_short_name
            FROM national_final
            JOIN year ON year.id = national_final.year_id
            WHERE national_final.id = %s
            """,
            (national_final_id,),
        )
        national_final = cursor.fetchone()
        if not national_final:
            return render_template("error.html", error="National final not found"), 404
        if national_final["status"] not in {"draft", "submissions"}:
            return render_template(
                "error.html", error="National final is not accepting candidates"
            ), 400
        if not permissions.can_view_restricted and national_final["owner_id"] != user[0]:
            return render_template("error.html", error="Not authorized"), 403
        year = str(national_final["year_id"])
        country = national_final["owner_country_id"] or country

    return render_template(
        "member/submit.html",
        year=year,
        country=country,
        entry_number=entry_number,
        elevated=permissions.can_edit,
        years=get_years_grouped(),
        languages=get_languages(),
        genre_options=get_genre_options(),
        countries=countries,
        data={},
        onLoad=True,
        users=get_users(),
        national_final=national_final,
    )


@bp.get("/submit/<int(signed=True):year>")
def get_countries_for_year(year: int):
    session_data = get_user_id_from_session(request.cookies.get("session"))
    user_id = session_data[0] if session_data else None
    permissions = get_user_permissions(user_id)
    national_final_id = request.args.get("national_final_id", type=int)
    if national_final_id is not None:
        cursor = get_db().cursor()
        cursor.execute(
            "SELECT owner_id, owner_country_id, status FROM national_final "
            "WHERE id = %s AND year_id = %s",
            (national_final_id, year),
        )
        nf = cursor.fetchone()
        if not nf or (not permissions.can_view_restricted and nf["owner_id"] != user_id):
            return {"error": "Not authorized"}, 403
        if nf["status"] not in {"draft", "submissions"}:
            return {"error": "National final is not accepting candidates"}, 400
        if nf["owner_country_id"]:
            cursor.execute(
                "SELECT id AS cc, name FROM country WHERE id = %s",
                (nf["owner_country_id"],),
            )
        else:
            cursor.execute("SELECT id AS cc, name FROM country WHERE id <> 'XX' ORDER BY name")
        return {
            "countries": {"own": [], "placeholder": cursor.fetchall(), "force_placeholder": False}
        }
    countries = get_countries(year, user_id, all=permissions.can_edit)
    return {"countries": countries}


@bp.get("/submit/<int(signed=True):year>/<country>")
def get_country_data(year: int, country: str):
    canonical = resolve_country_code(country.upper())
    if canonical and canonical.lower() != country.lower():
        return redirect(
            url_for("member.get_country_data", year=year, country=canonical.lower()), 301
        )

    db = get_db()
    cursor = db.cursor()

    # An explicit entry number addresses NF candidates and special entries
    # without relying on the country/year pair being unique.
    entry_raw = request.args.get("entry_number")
    if entry_raw is not None:
        try:
            entry_number = int(entry_raw)
        except (ValueError, TypeError):
            return {"error": "entry_number must be an integer"}, 400
        cursor.execute(
            """
            SELECT id, title, native_title, artist, is_placeholder,
                   title_language_id, native_language_id, video_link, poster_link,
                   vtt_link, snippet_start, snippet_end, snippet2_start, snippet2_end,
                   translated_lyrics,
                   romanized_lyrics, native_lyrics, notes, submitter_id,
                   sources, entry_number
            FROM current_song AS song
            WHERE year_id = %s AND country_id = %s AND entry_number = %s
        """,
            (year, country.upper(), entry_number),
        )
    elif year < 0:
        session_data = get_user_id_from_session(request.cookies.get("session"))
        current_user_id = session_data[0] if session_data else None
        cursor.execute(
            """
                SELECT id, title, native_title, artist, is_placeholder,
                       title_language_id, native_language_id, video_link, poster_link,
                       vtt_link, snippet_start, snippet_end, snippet2_start, snippet2_end,
                       translated_lyrics,
                       romanized_lyrics, native_lyrics, notes, submitter_id,
                       sources, entry_number
                FROM current_song AS song
                WHERE year_id = %s AND country_id = %s AND submitter_id = %s
                ORDER BY entry_number
                LIMIT 1
            """,
            (year, country.upper(), current_user_id),
        )
    else:
        cursor.execute(
            """
            SELECT id, title, native_title, artist, is_placeholder,
                   title_language_id, native_language_id, video_link, poster_link,
                   vtt_link, snippet_start, snippet_end, snippet2_start, snippet2_end,
                   translated_lyrics,
                   romanized_lyrics, native_lyrics, notes, submitter_id,
                   sources, entry_number
            FROM current_song AS song
            WHERE year_id = %s AND country_id = %s
        """,
            (year, country.upper()),
        )
    row = cursor.fetchone()
    if not row:
        return {"error": "Song not found"}, 404

    song_id = row["id"]

    cursor.execute(
        """
        SELECT language.id, language.name
        FROM current_song AS song
        JOIN language_set_language AS member
          ON member.language_set_id = song.language_set_id
        JOIN language ON member.language_id = language.id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    languages = [{"id": r["id"], "name": r["name"]} for r in cursor.fetchall()]

    cursor.execute(
        """
        SELECT start_seconds, tonic, mode, microtonal, notes
        FROM song_key_signature
        WHERE song_id = %s
        ORDER BY start_seconds
    """,
        (song_id,),
    )
    key_signatures = [
        {
            "start_seconds": r["start_seconds"],
            "tonic": r["tonic"],
            "mode": r["mode"],
            "microtonal": bool(r["microtonal"]),
            "notes": r["notes"],
        }
        for r in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT start_seconds, numerator, denominator, notes
        FROM song_time_signature
        WHERE song_id = %s
        ORDER BY start_seconds
    """,
        (song_id,),
    )
    time_signatures = [
        {
            "start_seconds": r["start_seconds"],
            "numerator": r["numerator"],
            "denominator": r["denominator"],
            "notes": r["notes"],
        }
        for r in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT subgenre.id, subgenre.name AS subgenre_name,
               genre.id AS genre_id, genre.name AS genre_name
        FROM song_subgenre
        JOIN subgenre ON subgenre.id = song_subgenre.subgenre_id
        JOIN genre ON genre.id = subgenre.genre_id
        WHERE song_subgenre.song_id = %s
        ORDER BY song_subgenre.priority
    """,
        (song_id,),
    )
    subgenres = [
        {
            "id": r["id"],
            "name": r["subgenre_name"],
            "genre_id": r["genre_id"],
            "genre_name": r["genre_name"],
        }
        for r in cursor.fetchall()
    ]

    return {
        "id": song_id,
        "year": year,
        "country": country,
        "entry_number": row["entry_number"],
        "title": row["title"],
        "native_title": row["native_title"],
        "artist": row["artist"],
        "is_placeholder": bool(row["is_placeholder"]),
        "title_language_id": row["title_language_id"],
        "native_language_id": row["native_language_id"],
        "video_link": row["video_link"],
        "poster_link": row["poster_link"],
        "vtt_link": row["vtt_link"],
        "snippet_start": (
            format_seconds(row["snippet_start"]) if row["snippet_start"] is not None else None
        ),
        "snippet_end": (
            format_seconds(row["snippet_end"]) if row["snippet_end"] is not None else None
        ),
        "snippet2_start": (
            format_seconds(row["snippet2_start"]) if row["snippet2_start"] is not None else None
        ),
        "snippet2_end": (
            format_seconds(row["snippet2_end"]) if row["snippet2_end"] is not None else None
        ),
        "translated_lyrics": row["translated_lyrics"],
        "romanized_lyrics": row["romanized_lyrics"],
        "native_lyrics": row["native_lyrics"],
        "notes": row["notes"],
        "sources": row["sources"],
        "user_id": row["submitter_id"] or 0,
        "languages": languages,
        "key_signatures": key_signatures,
        "time_signatures": time_signatures,
        "subgenres": subgenres,
    }
