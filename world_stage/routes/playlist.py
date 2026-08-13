import re
import unicodedata
import urllib.parse

from flask import Blueprint, Response, request, url_for

from .. import scrobble
from ..db import get_db
from ..utils import (
    UserPermissions,
    get_show_id,
    get_user_id_from_session,
    render_template,
    resolve_country_code,
    with_permissions,
    write_m3u,
)
from .year import generate_playlist

bp = Blueprint("playlist", __name__, url_prefix="/playlist")


def _scrobble_enabled() -> bool:
    user = get_user_id_from_session(request.cookies.get("session"))
    return bool(user) and scrobble.has_enabled_account(user[0])


def _play_entries(rows: list[dict], postcards: bool) -> tuple[list[dict], list[str]]:
    """Turn the same catalog rows used by an M3U into browser-player entries."""
    entries: list[dict] = []
    bad_countries: list[str] = []
    for index, row in enumerate(rows):
        cc = (row.get("cc") or "").lower()
        url = row.get("video_link") or ""
        shuffle_group = f"entry-{index}"
        if "media.world-stage.org" not in url:
            bad_countries.append(cc)
        if postcards:
            entries.append(
                {
                    "kind": "postcard",
                    "shuffle_group": shuffle_group,
                    "shuffleable": True,
                    "cc": cc,
                    "country": row.get("country") or "",
                    "title": "",
                    "artist": "",
                    "url": f"https://media.world-stage.org/postcards/{cc}.mov",
                    "poster": None,
                    "vtt": None,
                }
            )
        entries.append(
            {
                "kind": "song",
                "shuffle_group": shuffle_group,
                "shuffleable": True,
                "id": row["id"],
                "cc": cc,
                "country": row.get("country") or "",
                "title": row.get("title") or "",
                "artist": row.get("artist") or "",
                "duration": row.get("duration"),
                "url": url,
                "poster": row.get("poster_link") or None,
                "vtt": row.get("vtt_link") or None,
            }
        )
    return entries, bad_countries


def _render_player(
    *,
    rows: list[dict],
    permissions: UserPermissions,
    title: str,
    back_url: str,
    download_url: str,
):
    postcards = request.args.get("postcards", "false") == "true"
    entries, bad_countries = _play_entries(rows, postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err
    return render_template(
        "year/play.html",
        collection_title=title,
        collection_back_url=back_url,
        collection_download_url=download_url,
        entries=entries,
        postcards=postcards,
        scrobble_enabled=_scrobble_enabled(),
    )


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
@with_permissions
def show(key: str, permissions: UserPermissions):
    stem, postcards, include_host = _split_show_flags(key)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT show.short_name AS show_short, year.id AS year_id
        FROM show
        JOIN year ON year.id = show.year_id
        WHERE LOWER(LPAD(ABS(year.id)::text, 4, '0') || show.short_name) = LOWER(%(k)s)
           OR (year.special_short_name IS NOT NULL
               AND LOWER(year.special_short_name || show.short_name) = LOWER(%(k)s))
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
    cursor.execute("SELECT id FROM year WHERE LOWER(special_short_name) = LOWER(%s)", (stem,))
    row = cursor.fetchone()
    return row["id"] if row else None


def _year_rows(year_id: int) -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, LOWER(country.id) AS cc, country.name AS country,
               song.title, song.artist, song.duration, song.video_link,
               song.poster_link, song.vtt_link
        FROM current_song AS song
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
    return cursor.fetchall()


@bp.get("/year/<key>.m3u")
@with_permissions
def year(key: str, permissions: UserPermissions):
    stem, postcards = _split_np(key)

    year_id = _resolve_year(stem)
    if year_id is None:
        return render_template("error.html", error=f"Year not found: {stem}"), 404

    rows = _year_rows(year_id)
    if not rows:
        return render_template("error.html", error=f"No entries for {stem}"), 404

    entries = [(r["cc"], r["video_link"]) for r in rows]
    value, bad_countries = write_m3u(entries, postcards=postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)


@bp.get("/year/<key>/play")
@with_permissions
def year_play(key: str, permissions: UserPermissions):
    year_id = _resolve_year(key)
    if year_id is None:
        return render_template("error.html", error=f"Year not found: {key}"), 404
    rows = _year_rows(year_id)
    if not rows:
        return render_template("error.html", error=f"No entries for {key}"), 404

    if year_id < 0:
        back_url = url_for("year.special", short_name=key)
    else:
        back_url = url_for("year.year", year=year_id)
    return _render_player(
        rows=rows,
        permissions=permissions,
        title=f"{key}",
        back_url=back_url,
        download_url=url_for("playlist.year", key=key),
    )


@bp.get("/country/<key>.m3u")
@with_permissions
def country(key: str, permissions: UserPermissions):
    stem, postcards = _split_np(key)
    canonical = resolve_country_code(stem.upper())
    if not canonical:
        return render_template("error.html", error=f"Country not found: {stem}"), 404

    rows = _country_rows(canonical)
    if not rows:
        return render_template("error.html", error=f"No published entries for {canonical}"), 404

    entries = [(r["cc"], r["video_link"]) for r in rows]
    value, bad_countries = write_m3u(entries, postcards=postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)


def _country_rows(canonical: str) -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, LOWER(country.id) AS cc, country.name AS country,
               song.title, song.artist, song.duration, song.video_link,
               song.poster_link, song.vtt_link
        FROM current_song AS song
        JOIN country ON song.country_id = country.id
        JOIN year ON year.id = song.year_id
        WHERE (country.id = %(cc)s OR country.cc3 = %(cc)s)
          AND year.status IN ('closed', 'ongoing')
          AND NOT song.is_placeholder
        ORDER BY song.year_id
        """,
        {"cc": canonical},
    )
    return cursor.fetchall()


@bp.get("/country/<key>/play")
@with_permissions
def country_play(key: str, permissions: UserPermissions):
    canonical = resolve_country_code(key.upper())
    if not canonical:
        return render_template("error.html", error=f"Country not found: {key}"), 404
    rows = _country_rows(canonical)
    if not rows:
        return render_template("error.html", error=f"No published entries for {canonical}"), 404
    return _render_player(
        rows=rows,
        permissions=permissions,
        title=f"{rows[0]['country']}",
        back_url=url_for("country.country", code=canonical.lower()),
        download_url=url_for("playlist.country", key=canonical.lower()),
    )


@bp.get("/user/<key>.m3u")
@with_permissions
def user(key: str, permissions: UserPermissions):
    stem, postcards = _split_np(key)
    stem, normalized = _normalize_user_key(stem)

    rows = _user_rows(normalized)
    if not rows:
        return render_template("error.html", error=f"No published entries for {stem}"), 404

    entries = [(r["cc"], r["video_link"]) for r in rows]
    value, bad_countries = write_m3u(entries, postcards=postcards)
    err = _bad_links_error(bad_countries, permissions)
    if err:
        return err

    return _m3u(value, key)


def _normalize_user_key(stem: str) -> tuple[str, str]:
    stem = unicodedata.normalize("NFKC", urllib.parse.unquote(stem))
    return stem, re.sub(r"\s+", "_", stem).lower()


def _user_rows(normalized: str) -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, LOWER(country.id) AS cc, country.name AS country,
               song.title, song.artist, song.duration, song.video_link,
               song.poster_link, song.vtt_link, account.username
        FROM current_song AS song
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
    return cursor.fetchall()


@bp.get("/user/<key>/play")
@with_permissions
def user_play(key: str, permissions: UserPermissions):
    stem, normalized = _normalize_user_key(key)
    rows = _user_rows(normalized)
    if not rows:
        return render_template("error.html", error=f"No published entries for {stem}"), 404
    username = rows[0]["username"]
    return _render_player(
        rows=rows,
        permissions=permissions,
        title=f"{username}",
        back_url=url_for("user.submissions", username=username),
        download_url=url_for("playlist.user", key=username),
    )
