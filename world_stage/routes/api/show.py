from flask import Blueprint, request

from world_stage.db import get_db
from world_stage.utils import (
    ErrorID,
    err,
    get_points_for_system,
    get_show_id,
    require_api_auth,
    resp,
    url_bool,
)

from ...utils.voting import get_point_system
from .metadata import metadata_response

bp = Blueprint("show", __name__, url_prefix="/show")


def _show_id(key: str) -> int | None:
    show = get_show_id(key)
    return show.id if show else None


@bp.get("/<key>/metadata")
def show_metadata(key: str):
    return metadata_response("show", _show_id(key))


@bp.route("/<key>/metadata", methods=["POST", "PATCH", "PUT"])
@require_api_auth
def set_show_metadata(key: str, auth):
    if not auth[2].can_view_restricted:
        return err(ErrorID.FORBIDDEN, "Admin access required")
    return metadata_response("show", _show_id(key), write=True)


def _show_json(row: dict, points: list[int] | None = None) -> dict:
    local_short_name = row["short_name"]
    route_short_name = (
        f"{row['national_final_short_name']}-{local_short_name}"
        if row.get("national_final_short_name") else local_short_name
    )
    special = row["special_short_name"] is not None
    parent_name = row["special_name"] if special else str(row["year_id"])
    national_final_name = row.get("national_final_name")
    if national_final_name:
        display_name = f"{national_final_name}: {row['show_name']}"
    else:
        display_name = f"{parent_name} {row['show_name']}"
    if special:
        key = f"{row['special_short_name']}-{route_short_name}"
    else:
        key = f"{row['year_id']}-{route_short_name}"

    return {
        "id": row["id"],
        "key": key,
        "name": row["show_name"],
        "display_name": display_name,
        "short_name": route_short_name,
        "local_short_name": local_short_name,
        "national_final_short_name": row.get("national_final_short_name"),
        "year": row["year_id"],
        "special_name": row["special_name"],
        "special_short_name": row["special_short_name"],
        "date": row["date"],
        "status": row["status"],
        "point_system_id": row["point_system_id"],
        "points": points,
        "point_system": get_point_system(row["point_system_id"]),
        "voting_opens": row["voting_opens"],
        "voting_closes": row["voting_closes"],
        "predictions_close": row["predictions_close"],
        "progressions": row["progressions"],
    }


@bp.get("")
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
               show.predictions_close, show.date,
               show.status, year.special_name, year.special_short_name,
               national_final.short_name AS national_final_short_name,
               national_final.name AS national_final_name,
               COALESCE((
                   SELECT jsonb_agg(jsonb_build_object(
                       'target_show_id', progression.target_show_id,
                       'target_short_name', target.short_name,
                       'target_name', target.show_name,
                       'qualifier_count', progression.qualifier_count,
                       'priority', progression.priority
                   ) ORDER BY progression.priority)
                   FROM show_progression AS progression
                   JOIN show AS target ON target.id = progression.target_show_id
                   WHERE progression.source_show_id = show.id
               ), '[]'::jsonb) AS progressions
        FROM show
        JOIN show_types ON show_types.id = show.show_type
        JOIN year ON year.id = show.year_id
        LEFT JOIN national_final ON national_final.id = show.national_final_id
        {where}
        ORDER BY (national_final.id IS NOT NULL),
                 show.year_id, national_final.id NULLS FIRST,
                 show_types.sort_order, show.show_number NULLS FIRST, show.id
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
