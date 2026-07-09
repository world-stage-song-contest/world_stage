from collections import defaultdict

from flask import Blueprint, request

from world_stage.db import fetchone, get_db
from world_stage.routes.member import get_countries as get_submission_countries
from world_stage.utils import (
    UserPermissions,
    dt_now,
    format_timedelta,
    get_api_auth,
    get_points_for_system,
    resp,
    url_bool,
)

bp = Blueprint("discovery", __name__)


def _permissions_json(permissions: UserPermissions) -> dict:
    return {
        "role": permissions.role,
        "can_edit": permissions.can_edit,
        "can_view_restricted": permissions.can_view_restricted,
    }


def _show_json(row: dict, points: list[int] | None = None) -> dict:
    special = row["special_short_name"] is not None
    if special:
        key = f"{row['special_short_name']}-{row['short_name']}"
        display_name = f"{row['special_name']} {row['show_name']}"
    else:
        key = f"{row['year_id']}-{row['short_name']}"
        display_name = f"{row['year_id']} {row['show_name']}"

    return {
        "id": row["id"],
        "key": key,
        "name": row["show_name"],
        "display_name": display_name,
        "short_name": row["short_name"],
        "year": row["year_id"],
        "special_name": row["special_name"],
        "special_short_name": row["special_short_name"],
        "date": row["date"],
        "status": row["status"],
        "point_system_id": row["point_system_id"],
        "points": points,
        "voting_opens": row["voting_opens"],
        "voting_closes": row["voting_closes"],
        "predictions_close": row["predictions_close"],
        "dtf": row["dtf"],
        "sc": row["sc"],
        "special": row["special"],
    }


@bp.get("/me")
def me():
    auth = get_api_auth()
    if not auth:
        return resp(
            {
                "authenticated": False,
                "user": None,
                "permissions": _permissions_json(UserPermissions()),
            }
        )

    user_id, username, permissions = auth
    return resp(
        {
            "authenticated": True,
            "user": {"id": user_id, "username": username},
            "permissions": _permissions_json(permissions),
        }
    )


@bp.get("/language")
def languages():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, name, tag, extlang, region, subvariant, suppress_script, code3
        FROM language
        ORDER BY name
        """
    )
    return resp([dict(row) for row in cursor.fetchall()])


@bp.get("/genre")
def genres():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT genre.id AS genre_id, genre.name AS genre_name,
               subgenre.id AS subgenre_id, subgenre.name AS subgenre_name
        FROM genre
        LEFT JOIN subgenre ON subgenre.genre_id = genre.id
        ORDER BY genre.name,
                 (subgenre.name = genre.name) DESC,
                 subgenre.name
        """
    )

    grouped: dict[int, dict] = {}
    for row in cursor.fetchall():
        genre_id = row["genre_id"]
        if genre_id not in grouped:
            grouped[genre_id] = {
                "id": genre_id,
                "name": row["genre_name"],
                "subgenres": [],
            }
        if row["subgenre_id"] is not None:
            grouped[genre_id]["subgenres"].append(
                {"id": row["subgenre_id"], "name": row["subgenre_name"]}
            )

    return resp(list(grouped.values()))


@bp.get("/point-system")
def point_systems():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT point_system.id, point_system.number, point.place, point.score
        FROM point_system
        LEFT JOIN point ON point.point_system_id = point_system.id
        ORDER BY point_system.id, point.place
        """
    )

    systems: dict[int, dict] = {}
    for row in cursor.fetchall():
        system_id = row["id"]
        if system_id not in systems:
            systems[system_id] = {
                "id": system_id,
                "number": row["number"],
                "points": [],
            }
        if row["score"] is not None:
            systems[system_id]["points"].append(row["score"])

    return resp(list(systems.values()))


@bp.get("/show")
def shows():
    year = request.args.get("year", type=int)
    status = request.args.get("status")
    include_points = request.args.get("points", default="true", type=url_bool)

    clauses = []
    params = {}
    if year is not None:
        clauses.append("show.year_id = %(year)s")
        params["year"] = year
    if status:
        clauses.append("show.status = %(status)s")
        params["status"] = status

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    cursor = get_db().cursor()
    cursor.execute(
        f"""
        SELECT show.id, show.year_id, show.point_system_id, show.show_name,
               show.short_name, show.voting_opens, show.voting_closes,
               show.predictions_close, show.date, show.dtf, show.sc, show.special,
               show.status, year.special_name, year.special_short_name
        FROM show
        JOIN year ON year.id = show.year_id
        {where}
        ORDER BY show.year_id, show.date NULLS LAST, show.id
        """,
        params,
    )

    point_cache: dict[int, list[int]] = {}
    data = []
    for row in cursor.fetchall():
        points = None
        if include_points:
            point_system_id = row["point_system_id"]
            if point_system_id not in point_cache:
                point_cache[point_system_id] = get_points_for_system(point_system_id)
            points = point_cache[point_system_id]
        data.append(_show_json(row, points))
    return resp(data)


@bp.get("/voting/open")
def open_votings():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show.id, show.year_id, show.point_system_id, show.show_name,
               show.short_name, show.voting_opens, show.voting_closes,
               show.predictions_close, show.date, show.dtf, show.sc, show.special,
               show.status, year.special_name, year.special_short_name,
               COUNT(vote_set.id) AS vote_count
        FROM show
        JOIN year ON year.id = show.year_id
        LEFT JOIN vote_set ON vote_set.show_id = show.id
        WHERE show.voting_opens <= CURRENT_TIMESTAMP
          AND (show.voting_closes IS NULL OR show.voting_closes >= CURRENT_TIMESTAMP)
        GROUP BY show.id, year.id
        ORDER BY show.id
        """
    )

    data = []
    point_cache: dict[int, list[int]] = {}
    for row in cursor.fetchall():
        point_system_id = row["point_system_id"]
        if point_system_id not in point_cache:
            point_cache[point_system_id] = get_points_for_system(point_system_id)
        item = _show_json(row, point_cache[point_system_id])
        left = row["voting_closes"] - dt_now() if row["voting_closes"] else None
        pred_deadline = row["predictions_close"] or row["voting_closes"]
        item["predictions_open"] = not pred_deadline or pred_deadline >= dt_now()
        item["time_left"] = format_timedelta(left)
        item["vote_count"] = row["vote_count"]
        data.append(item)

    return resp(data)


@bp.get("/year/<int(signed=True):year>/submission-countries")
def submission_countries(year: int):
    auth = get_api_auth()
    user_id = auth[0] if auth else None
    permissions = auth[2] if auth else UserPermissions()
    show_all = request.args.get("all", default=str(permissions.can_edit).lower(), type=url_bool)

    countries = get_submission_countries(year, user_id, all=show_all)
    return resp(countries)


@bp.get("/submission-context")
def submission_context():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, status, special_name, special_short_name
        FROM year
        ORDER BY id
        """
    )
    years: dict[str, list] = defaultdict(list)
    for row in cursor.fetchall():
        if row["id"] < 0:
            years["specials"].append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "special_name": row["special_name"],
                    "special_short_name": row["special_short_name"],
                }
            )
        elif row["status"] == "open":
            years["open"].append(row["id"])
        else:
            years["closed"].append(row["id"])

    cursor.execute("SELECT COUNT(*) AS c FROM song WHERE year_id = ANY(%s)", (years["open"],))
    open_song_count = fetchone(cursor)["c"] if years["open"] else 0

    return resp(
        {
            "years": {
                "open": years["open"],
                "closed": years["closed"],
                "specials": years["specials"],
            },
            "languages_url": "/api/language",
            "genres_url": "/api/genre",
            "open_song_count": open_song_count,
        }
    )
