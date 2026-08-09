from flask import Blueprint, redirect, request, url_for

from world_stage.db import get_db
from world_stage.models import Country
from world_stage.utils import ErrorID, err, resolve_country_code, resp, url_bool

from .song import _fetch_country_song_list, _song_list_rows_to_json

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
    requested_code = id.upper()
    canonical = (
        requested_code if len(requested_code) == 2 else resolve_country_code(requested_code)
    )
    if canonical and canonical.upper() != id.upper():
        return redirect(url_for("api.country.songs", id=canonical), 301)

    db = get_db()
    cursor = db.cursor()
    rows = _fetch_country_song_list(cursor, canonical or requested_code)
    return resp(_song_list_rows_to_json(rows))
