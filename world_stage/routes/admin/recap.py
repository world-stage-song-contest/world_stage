import csv
import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, LiteralString

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
    params_list = [(country, country, country) for country in form_data]
    return _lookup_many(
        cursor,
        """
        SELECT id FROM country
        WHERE LOWER(id) = LOWER(%s)
           OR LOWER(cc3) = LOWER(%s)
           OR LOWER(name) = LOWER(%s)
        LIMIT 1
        """,
        params_list,
        not_found_msg=lambda p: f"Country '{p[0]}' is unknown",
    )


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
           show.id as show_id, show.year_id AS year, account.username AS submitter,
           show.year_id || short_name AS show, running_order AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           (SELECT MAX(changed_at) FROM song_audit_log WHERE song_id = song.id) AS _changed_at,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song_show
    JOIN song ON song_show.song_id = song.id
    JOIN show ON song_show.show_id = show.id
    JOIN country ON song.country_id = country.id
    LEFT JOIN account ON song.submitter_id = account.id
    WHERE show.id = ANY(%s) AND (%s OR (show.year_id < 0) = %s)
    ORDER BY song.id, show.id
)
SELECT year, submitter, show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link, _changed_at
FROM song_data
ORDER BY show_id, ro
"""

_SQL_YEAR = """
WITH song_data AS (
    SELECT song.year_id as show_id, song.year_id AS year, account.username AS submitter,
           song.year_id AS show, UPPER(country.id) AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           (SELECT MAX(changed_at) FROM song_audit_log WHERE song_id = song.id) AS _changed_at,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    LEFT JOIN account ON song.submitter_id = account.id
    WHERE song.year_id = ANY(%s) AND (%s OR (song.year_id < 0) = %s)
    ORDER BY LOWER(country.id)
)
SELECT year, submitter, show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link, _changed_at
FROM song_data
ORDER BY country
"""

_SQL_COUNTRY = """
WITH song_data AS (
    SELECT song.year_id AS year, account.username AS submitter,
           LOWER(country.id) as show_id, LOWER(country.id) AS show,
           MOD(song.year_id, 100) AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           (SELECT MAX(changed_at) FROM song_audit_log WHERE song_id = song.id) AS _changed_at,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    JOIN year ON song.year_id = year.id
    LEFT JOIN account ON song.submitter_id = account.id
    WHERE country.id = ANY(%s) AND year_id IS NOT NULL AND year.status = 'closed'
      AND (%s OR (song.year_id < 0) = %s)
    ORDER BY song.year_id
)
SELECT year, submitter, show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link, _changed_at
FROM song_data
ORDER BY year
"""

_SQL_SUBMITTER = """
WITH song_data AS (
    SELECT account.username as show_id, song.year_id AS year, account.username AS submitter,
           account.username AS show,
           MOD(song.year_id, 100) AS ro,
           LOWER(country.id) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           (SELECT MAX(changed_at) FROM song_audit_log WHERE song_id = song.id) AS _changed_at,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    JOIN year ON song.year_id = year.id
    JOIN account ON song.submitter_id = account.id
    WHERE song.submitter_id = ANY(%s) AND year_id IS NOT NULL AND year.status = 'closed'
      AND (%s OR (song.year_id < 0) = %s)
    ORDER BY song.year_id, country
)
SELECT year, submitter, show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link, _changed_at
FROM song_data
ORDER BY year, country
"""

_SPECS: dict[str, Spec] = {
    "show": Spec(parse=_parse_show_ids, sql=_SQL_SHOW),
    "year": Spec(parse=_parse_years, sql=_SQL_YEAR),
    "country": Spec(parse=_parse_countries, sql=_SQL_COUNTRY),
    "submitter": Spec(parse=_parse_submitter_ids, sql=_SQL_SUBMITTER),
}


def get_recap_data(
    mode: str,
    form_data: list[str],
    *,
    specials: Literal["false", "true", "only"] = "false",
    include_change_metadata: bool = False,
) -> list[dict] | None:
    db = get_db()
    cursor = db.cursor()

    spec = _SPECS.get(mode)
    if not spec:
        return None

    try:
        param = spec.parse(form_data, cursor)
        cursor.execute(spec.sql, (param, specials == "true", specials == "only"))
        data = cursor.fetchall()
        if not include_change_metadata:
            for row in data:
                row.pop("_changed_at", None)
        return data
    except BadRecapRequestError:
        return None


def drop_none(obj):
    if isinstance(obj, dict):
        return {k: drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [drop_none(v) for v in obj]
    return obj


_MEDIA_URL = "https://media.world-stage.org"

_OPENING_ACT_PLACEMENTS = {
    "f": 1,
    "sc": 2,
    "sf4": 3,
    "sf3": 4,
    "sf2": 5,
    "sf1": 6,
}


def _get_opening_act_country(cursor, year: int, short_name: str) -> str | None:
    placement = _OPENING_ACT_PLACEMENTS.get(short_name)
    if placement is None:
        return None

    cursor.execute(
        """
        SELECT LOWER(country_id) AS cc
        FROM country_year_results
        WHERE year_id = %s AND place = %s
        """,
        (year - 1, placement),
    )
    row = cursor.fetchone()
    return row["cc"] if row else None


def get_cytube_playlist(form_data: list[str]) -> str | None:
    """Build a CyTube import playlist for one regular show."""
    if len(form_data) != 1:
        return None

    try:
        year, short_name = _parse_show_key(form_data[0])
    except BadRecapRequestError:
        return None

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id FROM show
        WHERE year_id = %s AND short_name = %s
        """,
        (year, short_name),
    )
    show = cursor.fetchone()
    if not show:
        return None

    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc, country.name AS country, song.artist, song.title
        FROM song_show
        JOIN song ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY song_show.running_order
        """,
        (show["id"],),
    )
    songs = cursor.fetchall()

    # The host performs halfway through odd-numbered semi-finals, but is not
    # normally assigned to a semi-final's song_show rows.
    if short_name in ("sf1", "sf3"):
        cursor.execute(
            """
            SELECT LOWER(country.id) AS cc, country.name AS country, song.artist, song.title
            FROM year
            JOIN country ON year.host_id = country.id
            JOIN song ON song.year_id = year.id AND song.country_id = year.host_id
            WHERE year.id = %s
            ORDER BY song.entry_number
            LIMIT 1
            """,
            (year,),
        )
        host = cursor.fetchone()
        if host:
            midpoint = (len(songs) + 1) // 2
            songs.insert(midpoint, {**host, "is_host": True})

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow((f"WS {year} Opening", f"{_MEDIA_URL}/openings/{year}.mov"))

    opening_act_country = _get_opening_act_country(cursor, year, short_name)
    if opening_act_country:
        writer.writerow(("Opening act", f"{_MEDIA_URL}/ws{year - 1}{opening_act_country}.json"))

    for song in songs:
        cc = song["cc"]
        is_host = song.get("is_host", False)
        postcard_name = f"[HOST] {song['country']}" if is_host else ""
        song_name = (
            f"[HOST] {song['artist'] or ''} - {song['title'] or ''}" if is_host else ""
        )
        writer.writerow((postcard_name, f"{_MEDIA_URL}/postcards/{cc}.mov"))
        writer.writerow((song_name, f"{_MEDIA_URL}/ws{year}{cc}.json"))

    writer.writerow(("Voting announcement", f"{_MEDIA_URL}/silence/silence.mov"))
    writer.writerow(("Recap 1", f"{_MEDIA_URL}/recaps/{year}{short_name}.mov"))
    writer.writerow(("", f"{_MEDIA_URL}/intervals/{year}/{short_name}/i1.json"))
    writer.writerow(("Recap 2", f"{_MEDIA_URL}/recaps/{year}{short_name}s.mov"))
    writer.writerow(("", f"{_MEDIA_URL}/intervals/{year}/{short_name}/i2.json"))
    writer.writerow(("", f"{_MEDIA_URL}/countdown/countdown_with_sound.json"))
    writer.writerow(("", f"{_MEDIA_URL}/intervals/{year}/{short_name}/i3.json"))
    return output.getvalue()


@bp.post("/recapdata")
def recap_data_post() -> Response | tuple[Response, int]:
    form_data = request.form.getlist("show")
    type = request.form.get("type", "")
    action = request.form.get("action", "render")
    pretty = request.form.get("pretty", "off") == "on"
    indent = 2 if pretty else None

    if action == "cytube":
        cytube_data = get_cytube_playlist(form_data)
        if cytube_data is None:
            return render_template("error.html", error="An error has occured")
        return render_template("admin/recap_data.html", data=cytube_data)

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
