from flask import Blueprint, request

from world_stage.db import get_db
from world_stage.models import Country, Year
from world_stage.utils import ErrorID, err, resp

from .song import _song_rows_to_json

bp = Blueprint("year", __name__, url_prefix="/year")


@bp.get("/")
def index():
    kind = request.args.get("type", "")

    match kind:
        case "open" | "closed" | "ongoing":
            status = kind
        case _:
            status = None

    db = get_db()
    cursor = db.cursor()

    if status is not None:
        cursor.execute(
            """
SELECT year.id, year.status, year.host_id, country.name, country.cc3
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
WHERE year.status = %s
ORDER BY year.id
""",
            (status,),
        )
    else:
        cursor.execute("""
SELECT year.id, year.status, year.host_id, country.name, country.cc3
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
ORDER BY year.id
""")

    data = [
        Year(
            year=val["id"],
            status=val["status"],
            host=Country(id=val["host_id"], cc3=val["cc3"], name=val["name"])
            if val["host_id"] is not None
            else None,
        ).to_json()
        for val in cursor.fetchall()
    ]
    return resp(data)


@bp.get("/<int:id>")
def year(id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
SELECT year.id, year.status, year.host_id, country.name, country.cc3,
       COUNT(song.is_placeholder) FILTER(WHERE song.is_placeholder = false) AS entries,
       COUNT(song.is_placeholder) FILTER(WHERE song.is_placeholder = true) AS placeholders
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
JOIN song ON song.year_id = year.id
WHERE year.id = %s
GROUP BY year.id, country.id
ORDER BY year.id
""",
        (id,),
    )

    val = cursor.fetchone()
    if not val:
        return err(ErrorID.NOT_FOUND, f"Country {id} not found")

    data = Year(
        year=val["id"],
        status=val["status"],
        entry_count=val["entries"],
        placeholder_count=val["placeholders"],
        host=Country(id=val["host_id"], cc3=val["cc3"], name=val["name"])
        if val["host_id"] is not None
        else None,
    ).to_json()
    return resp(data)


@bp.get("/<int:id>/songs")
def songs(id: int):
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
        WHERE song.year_id IS NOT NULL AND song.year_id = %(year)s
        ORDER BY song.year_id, country.name, song.entry_number
    """,
        {"year": id},
    )

    return resp(_song_rows_to_json(cursor, cursor.fetchall()))
