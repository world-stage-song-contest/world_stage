import unicodedata

import psycopg
from flask import Blueprint, redirect, request, url_for

from ..db import get_db
from ..utils import (
    UserPermissions,
    render_template,
    require_permissions,
    with_permissions,
)
from ..utils.artists import artist_display_name, parse_artist_display_name

bp = Blueprint("artist", __name__, url_prefix="/artist")


def _find_artist(cursor, display_name: str) -> dict | None:
    full_name, number = parse_artist_display_name(display_name)
    cursor.execute(
        """
        SELECT id, full_name, native_name, number
        FROM artist
        WHERE LOWER(full_name) = LOWER(%s) AND number = %s
        ORDER BY id
        LIMIT 1
        """,
        (full_name, number),
    )
    artist = cursor.fetchone()
    if artist:
        artist["display_name"] = artist_display_name(
            artist["full_name"], artist["number"]
        )
    return artist


@bp.get("")
@with_permissions
def index(permissions: UserPermissions):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT artist.id, artist.full_name, artist.native_name, artist.number,
               COUNT(DISTINCT song.id) AS entry_count
        FROM artist
        JOIN artist_credit AS credit ON credit.artist_id = artist.id
        JOIN current_song AS song
          ON song.artist_credit_set_id = credit.artist_credit_set_id
        JOIN year ON year.id = song.year_id
                 AND year.status IN ('closed', 'ongoing')
        GROUP BY artist.id
        ORDER BY LOWER(artist.full_name), artist.number, artist.id
        """
    )
    artists = cursor.fetchall()
    for artist in artists:
        artist["display_name"] = artist_display_name(
            artist["full_name"], artist["number"]
        )
    return render_template(
        "artist/index.html", artists=artists, can_edit=permissions.can_edit
    )


@bp.get("/<path:name>/edit")
@require_permissions(lambda permissions: permissions.can_edit)
def edit(name: str, permissions: UserPermissions):
    artist = _find_artist(get_db().cursor(), name)
    if not artist:
        return render_template("error.html", error="Artist not found"), 404
    return render_template("artist/edit.html", artist=artist, error=None)


@bp.post("/<path:name>/edit")
@require_permissions(lambda permissions: permissions.can_edit)
def update(name: str, permissions: UserPermissions):
    db = get_db()
    cursor = db.cursor()
    artist = _find_artist(cursor, name)
    if not artist:
        return render_template("error.html", error="Artist not found"), 404

    full_name = unicodedata.normalize(
        "NFC", (request.form.get("full_name") or "").strip()
    )
    native_name = unicodedata.normalize(
        "NFC", (request.form.get("native_name") or "").strip()
    ) or None
    number = request.form.get("number", type=int)
    error = None
    if not full_name:
        error = "Full name is required."
    elif len(full_name) > 150:
        error = "Full name must be at most 150 characters."
    elif native_name is not None and len(native_name) > 150:
        error = "Native name must be at most 150 characters."
    elif number is None or number < 1:
        error = "Artist number must be a positive integer."
    if error:
        attempted = dict(artist)
        attempted.update(
            full_name=full_name, native_name=native_name, number=number or 1
        )
        return render_template("artist/edit.html", artist=attempted, error=error), 400

    assert number is not None
    try:
        cursor.execute(
            """
            UPDATE artist
            SET full_name = %s, native_name = %s, number = %s
            WHERE id = %s
            """,
            (full_name, native_name, number, artist["id"]),
        )
        db.commit()
    except psycopg.errors.UniqueViolation:
        db.rollback()
        attempted = dict(artist)
        attempted.update(
            full_name=full_name, native_name=native_name, number=number
        )
        return render_template(
            "artist/edit.html",
            artist=attempted,
            error=f"{artist_display_name(full_name, number)} already exists.",
        ), 409

    return redirect(
        url_for("artist.details", name=artist_display_name(full_name, number))
    )


@bp.get("/<path:name>")
@with_permissions
def details(name: str, permissions: UserPermissions):
    cursor = get_db().cursor()
    artist = _find_artist(cursor, name)
    if not artist:
        return render_template("error.html", error="Artist not found"), 404
    cursor.execute(
        """
        SELECT DISTINCT song.id, song.year_id, song.country_id,
               country.name AS country_name, song.entry_number,
               song.title, song.artist, year.special_short_name,
               year.special_name
        FROM current_song AS song
        JOIN artist_credit AS credit
          ON credit.artist_credit_set_id = song.artist_credit_set_id
        JOIN country ON country.id = song.country_id
        JOIN year ON year.id = song.year_id
        WHERE credit.artist_id = %s
          AND year.status IN ('closed', 'ongoing')
        ORDER BY song.year_id DESC, country.name, song.entry_number
        """,
        (artist["id"],),
    )
    entries = cursor.fetchall()
    if not entries:
        return render_template("error.html", error="Artist not found"), 404
    return render_template(
        "artist/details.html",
        artist=artist,
        entries=entries,
        can_edit=permissions.can_edit,
    )
