from flask import Blueprint, redirect, request, url_for

from world_stage.db import get_db
from world_stage.models import Country
from world_stage.utils import ErrorID, err, resolve_country_code, resp, url_bool

from .song import _song_rows_to_json

bp = Blueprint("country", __name__, url_prefix="/country")


@bp.get("/")
def index():
    all = request.args.get("all", type=url_bool)

    if all:
        query = "SELECT id, name, cc3 FROM country WHERE id <> 'XX' ORDER BY name"
    else:
        query = (
            "SELECT id, name, cc3 FROM country WHERE is_participating AND id <> 'XX' ORDER BY name"
        )
    db = get_db()
    cursor = db.cursor()

    cursor.execute(query)

    data = [Country(**val).to_json() for val in cursor.fetchall()]
    return resp(data)


@bp.get("/<id>")
def country(id: str):
    canonical = resolve_country_code(id.upper())
    if canonical and canonical.upper() != id.upper():
        return redirect(url_for("api.country.country", id=canonical), 301)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "SELECT id, name, cc3 FROM country WHERE id = %s ORDER BY name",
        (canonical or id.upper(),),
    )

    res = cursor.fetchone()
    if not res:
        return err(ErrorID.NOT_FOUND, f"Country {id} not found")

    return resp(Country(**res).to_json())


@bp.get("/<id>/songs")
def songs(id: str):
    canonical = resolve_country_code(id.upper())
    if canonical and canonical.upper() != id.upper():
        return redirect(url_for("api.country.songs", id=canonical), 301)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT song.id, song.year_id, song.country_id, country.name AS country_name,
               song.title, song.native_title, song.artist, song.is_placeholder,
               song.title_language_id, song.native_language_id,
               song.video_link, song.poster_link, song.vtt_link,
               song.snippet_start, song.snippet_end,
               song.translated_lyrics, song.romanized_lyrics, song.native_lyrics,
               song.notes, song.sources, song.admin_approved,
               song.submitter_id, account.username, song.entry_number,
               song.duration, year.special_short_name
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT JOIN year ON year.id = song.year_id
        LEFT JOIN account ON song.submitter_id = account.id
        WHERE (song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id IS NOT NULL
        ORDER BY song.year_id, country.name, song.entry_number
    """,
        {"cc": canonical or id.upper()},
    )

    return resp(_song_rows_to_json(cursor, cursor.fetchall()))
