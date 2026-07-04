import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

from flask import Response, request

from ...db import get_db
from ...utils import (
    render_template,
)
from .common import bp


@bp.get("/recapdata")
def recap_data():
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
        "SELECT id FROM account WHERE LOWER(username) = LOWER(%s)",
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
