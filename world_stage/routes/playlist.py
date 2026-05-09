import re
import unicodedata
import urllib.parse

from flask import Blueprint, Response, request

from ..db import get_db
from ..utils import (
    get_show_id,
    get_user_role_from_session,
    render_template,
    resolve_country_code,
    write_m3u,
)
from .year import generate_playlist

bp = Blueprint("playlist", __name__, url_prefix="/playlist")


def _split_np(stem: str) -> tuple[str, bool]:
    if stem.endswith("-np"):
        return stem[:-3], False
    return stem, True


def _split_show_flags(stem: str) -> tuple[str, bool, bool]:
    """Strip ``-np``/``-nh`` flags from the stem in any order. Returns
    ``(stem, postcards, include_host)``."""
    postcards = True
    include_host = True
    while True:
        if stem.endswith("-np"):
            postcards = False
            stem = stem[:-3]
        elif stem.endswith("-nh"):
            include_host = False
            stem = stem[:-3]
        else:
            return stem, postcards, include_host


def _bad_links_error(bad_countries, permissions):
    if permissions.can_view_restricted or not bad_countries:
        return None
    missing = sorted(set(bad_countries))
    return render_template(
        "error.html",
        error=(
            "Not all video links are set yet. Ping a moderator. "
            f"Missing: {', '.join(missing)}."
        ),
    )


def _m3u(value: str, filename_stem: str) -> Response:
    return Response(
        value,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": f"attachment; filename={filename_stem}.m3u"},
    )


@bp.get("/show/<key>.m3u")
def show(key: str):
    stem, postcards, include_host = _split_show_flags(key)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT show.short_name AS show_short, year.id AS year_id
        FROM show
        JOIN year ON year.id = show.year_id
        WHERE LPAD(ABS(year.id)::text, 4, '0') || show.short_name = %(k)s
           OR (year.special_short_name IS NOT NULL
               AND year.special_short_name || show.short_name = %(k)s)
        LIMIT 1
        """,
        {"k": stem},
    )
    row = cursor.fetchone()
    if not row:
        return render_template("error.html", error=f"Show not found: {stem}"), 404

    show_data = get_show_id(row["show_short"], row["year_id"])
    if not show_data:
        return render_template("error.html", error=f"Show not found: {stem}"), 404

    value, bad_countries = generate_playlist(show_data, postcards, include_host)

    permissions = get_user_role_from_session(request.cookies.get("session"))
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)


def _resolve_year(stem: str) -> int | None:
    try:
        return int(stem)
    except ValueError:
        pass
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM year WHERE special_short_name = %s", (stem,))
    row = cursor.fetchone()
    return row["id"] if row else None


@bp.get("/year/<key>.m3u")
def year(key: str):
    stem, postcards = _split_np(key)

    year_id = _resolve_year(stem)
    if year_id is None:
        return render_template("error.html", error=f"Year not found: {stem}"), 404

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc, song.video_link
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT JOIN alternative_name an
            ON an.country_id = song.country_id
           AND (an.from_year_id IS NULL OR song.year_id >= an.from_year_id)
           AND (an.to_year_id IS NULL OR song.year_id <= an.to_year_id)
        WHERE song.year_id = %s AND NOT song.is_placeholder
        ORDER BY COALESCE(an.name, country.name)
        """,
        (year_id,),
    )
    entries = [(r["cc"], r["video_link"]) for r in cursor.fetchall()]
    if not entries:
        return render_template("error.html", error=f"No entries for {stem}"), 404

    permissions = get_user_role_from_session(request.cookies.get("session"))
    value, bad_countries = write_m3u(entries, postcards=postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)


@bp.get("/country/<key>.m3u")
def country(key: str):
    stem, postcards = _split_np(key)
    canonical = resolve_country_code(stem.upper())
    if not canonical:
        return render_template("error.html", error=f"Country not found: {stem}"), 404

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc, song.video_link
        FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON year.id = song.year_id
        WHERE (country.id = %(cc)s OR country.cc3 = %(cc)s)
          AND year.status IN ('closed', 'ongoing')
          AND NOT song.is_placeholder
        ORDER BY song.year_id
        """,
        {"cc": canonical},
    )
    entries = [(r["cc"], r["video_link"]) for r in cursor.fetchall()]
    if not entries:
        return render_template("error.html", error=f"No published entries for {canonical}"), 404

    permissions = get_user_role_from_session(request.cookies.get("session"))
    value, bad_countries = write_m3u(entries, postcards=postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)


@bp.get("/user/<key>.m3u")
def user(key: str):
    stem, postcards = _split_np(key)
    stem = unicodedata.normalize("NFKC", urllib.parse.unquote(stem))
    normalized = re.sub(r"\s+", "_", stem).lower()

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc, song.video_link, account.username
        FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON year.id = song.year_id
        JOIN account ON account.id = song.submitter_id
        WHERE LOWER(REGEXP_REPLACE(account.username, '\\s+', '_', 'g')) = %s
          AND year.status IN ('closed', 'ongoing')
          AND NOT song.is_placeholder
        ORDER BY song.year_id
        """,
        (normalized,),
    )
    rows = cursor.fetchall()
    if not rows:
        return render_template("error.html", error=f"No published entries for {stem}"), 404

    entries = [(r["cc"], r["video_link"]) for r in rows]
    permissions = get_user_role_from_session(request.cookies.get("session"))
    value, bad_countries = write_m3u(entries, postcards=postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)
