from flask import Blueprint, request

from world_stage.db import get_db
from world_stage.models import Country, Year
from world_stage.utils import ErrorID, err, resp

from .song import _fetch_year_song_list, _song_list_rows_to_json

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
SELECT year.id, year.status, year.submissions_open,
       year.host_id, country.name, country.cc3
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
WHERE year.status = %s
ORDER BY year.id
""",
            (status,),
        )
    else:
        cursor.execute("""
SELECT year.id, year.status, year.submissions_open,
       year.host_id, country.name, country.cc3
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
ORDER BY year.id
""")

    data = [
        Year(
            year=val["id"],
            status=val["status"],
            submissions_open=val["submissions_open"],
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
SELECT year.id, year.status, year.submissions_open,
       year.host_id, country.name, country.cc3,
       COUNT(song.is_placeholder) FILTER(WHERE song.is_placeholder = false) AS entries,
       COUNT(song.is_placeholder) FILTER(WHERE song.is_placeholder = true) AS placeholders
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
JOIN current_song AS song ON song.year_id = year.id
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
        submissions_open=val["submissions_open"],
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
    return resp(_song_list_rows_to_json(_fetch_year_song_list(cursor, id)))
