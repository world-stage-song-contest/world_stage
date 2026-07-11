from typing import Any

from flask import Blueprint, redirect, request, url_for

from ..db import fetchone, get_db
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

bp = Blueprint("member", __name__, url_prefix="/member")

MAX_USER_SUBMISSIONS = 2
MAX_YEAR_SUBMISSIONS = 73


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
        grouped[gid]["subgenres"].append(
            {"id": r["subgenre_id"], "name": r["subgenre_name"]}
        )
    return list(grouped.values())


def get_countries(year: int, user_id: int | None, all: bool = False) -> dict[str, list[dict]]:
    """Get countries available for submission"""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT status FROM year WHERE id = %s", (year,))
    year_result = cursor.fetchone()
    if not year_result:
        return {"own": [], "placeholder": []}

    closed = year_result["status"] != "open"
    is_special = year < 0
    user_limit = 1 if is_special else MAX_USER_SUBMISSIONS

    cursor.execute(
        "SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder", (year,)
    )
    year_count = fetchone(cursor)["c"]

    cursor.execute(
        """
        SELECT COUNT(*) AS c FROM song
        WHERE submitter_id = %s AND year_id = %s AND NOT is_placeholder
    """,
        (user_id, year),
    )
    user_count = fetchone(cursor)["c"]

    countries: dict[str, list[dict[str, Any]]] = {"own": [], "placeholder": []}

    # Get user's own submissions
    cursor.execute(
        """
        SELECT country.name, country.id AS cc FROM song
        JOIN country ON song.country_id = country.id
        WHERE song.year_id = %s AND song.submitter_id = %s
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
        if closed and not is_special:
            cursor.execute(
                """
                SELECT country.name, country.id AS cc FROM song
                JOIN country ON song.country_id = country.id
                WHERE song.year_id = %(year)s
                  AND submitter_id IS DISTINCT FROM %(user)s
                ORDER BY country.name
            """,
                {"year": year, "user": user_id},
            )
        else:
            cursor.execute(
                f"""
                SELECT name, id AS cc FROM country
                WHERE {availability_filter}
                      AND id NOT IN (
                          SELECT country_id FROM song
                          WHERE year_id = %(year)s AND submitter_id = %(user)s
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
                SELECT 1 FROM song AS s
                WHERE s.year_id = %(year)s AND s.country_id = c.id
                  AND s.submitter_id = %(user)s
              )
            ORDER BY c.name
        """,
            {"year": year, "user": user_id},
        )
        countries["placeholder"] = cursor.fetchall()
    elif (
        not is_special
        and user_count < MAX_USER_SUBMISSIONS
        and not closed
        and year_count < MAX_YEAR_SUBMISSIONS
    ):
        cursor.execute(
            f"""
            SELECT c.name, c.id AS cc
            FROM country AS c
            WHERE {availability_filter}
              AND NOT EXISTS (
                SELECT 1
                FROM song AS s
                WHERE s.year_id = %(year)s
                AND s.country_id = c.id
                AND s.is_placeholder = FALSE
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
def index(user: tuple[int, str]):
    return render_template("member/index.html", username=user[1])


@bp.get("/submit")
@require_user(redirect_to_login=True)
@with_permissions
def submit(user: tuple[int, str], permissions: UserPermissions):
    year = request.args.get("year")
    country = request.args.get("country")
    entry_number = request.args.get("entry_number")

    return render_template(
        "member/submit.html",
        year=year,
        country=country,
        entry_number=entry_number,
        elevated=permissions.can_edit,
        years=get_years_grouped(),
        languages=get_languages(),
        genre_options=get_genre_options(),
        countries={},
        data={},
        onLoad=True,
        users=get_users(),
    )


@bp.get("/submit/<int(signed=True):year>")
def get_countries_for_year(year: int):
    session_data = get_user_id_from_session(request.cookies.get("session"))
    user_id = session_data[0] if session_data else None
    permissions = get_user_permissions(user_id)
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

    # For specials, a country can have multiple entries. Since each user
    # can only submit one song per special, default to fetching the
    # current user's own entry for that country. An explicit entry_number
    # query parameter overrides (used by admins to edit other people's).
    if year < 0:
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
                       vtt_link, snippet_start, snippet_end, translated_lyrics,
                       romanized_lyrics, native_lyrics, notes, submitter_id,
                       sources, admin_approved, entry_number
                FROM song
                WHERE year_id = %s AND country_id = %s AND entry_number = %s
            """,
                (year, country.upper(), entry_number),
            )
        else:
            session_data = get_user_id_from_session(request.cookies.get("session"))
            current_user_id = session_data[0] if session_data else None
            cursor.execute(
                """
                SELECT id, title, native_title, artist, is_placeholder,
                       title_language_id, native_language_id, video_link, poster_link,
                       vtt_link, snippet_start, snippet_end, translated_lyrics,
                       romanized_lyrics, native_lyrics, notes, submitter_id,
                       sources, admin_approved, entry_number
                FROM song
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
                   vtt_link, snippet_start, snippet_end, translated_lyrics,
                   romanized_lyrics, native_lyrics, notes, submitter_id,
                   sources, admin_approved, entry_number
            FROM song
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
        FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_id = %s
        ORDER BY priority
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
        "snippet_start": format_seconds(row["snippet_start"] or None),
        "snippet_end": format_seconds(row["snippet_end"] or None),
        "translated_lyrics": row["translated_lyrics"],
        "romanized_lyrics": row["romanized_lyrics"],
        "native_lyrics": row["native_lyrics"],
        "notes": row["notes"],
        "sources": row["sources"],
        "admin_approved": row["admin_approved"],
        "user_id": row["submitter_id"] or 0,
        "languages": languages,
        "key_signatures": key_signatures,
        "time_signatures": time_signatures,
        "subgenres": subgenres,
    }
