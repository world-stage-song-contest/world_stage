from flask import Blueprint

from world_stage.db import get_db
from world_stage.utils import ErrorID, UserPermissions, dt_now, err, get_api_auth, get_show_id, resp

bp = Blueprint("results", __name__, url_prefix="/results")


def _show_and_year(key: str):
    show = get_show_id(key)
    if not show or not show.id:
        return None, None, err(ErrorID.NOT_FOUND, "Show not found")

    cursor = get_db().cursor()
    cursor.execute("SELECT status FROM year WHERE id = %s", (show.year,))
    year = cursor.fetchone()
    if not year:
        return None, None, err(ErrorID.NOT_FOUND, "Year not found")
    return show, year["status"], None


def _restricted_permissions() -> UserPermissions:
    auth = get_api_auth()
    return auth[2] if auth else UserPermissions()


def _public_access(show, year_status: str, permissions: UserPermissions):
    if permissions.can_view_restricted:
        return "full", None
    if year_status == "open":
        return None, err(ErrorID.FORBIDDEN, "Results are not published for an open year")
    if show.status == "none":
        return None, err(ErrorID.BAD_REQUEST, "This show has no published results")
    if show.status not in ("draw", "partial", "full"):
        return None, err(ErrorID.FORBIDDEN, "Results are not published for this show")
    return show.status, None


def _show_json(show, key: str, year_status: str) -> dict:
    return {
        "id": show.id,
        "key": key,
        "year": show.year,
        "year_status": year_status,
        "name": show.name,
        "short_name": show.short_name,
        "status": show.status,
        "points": show.points,
        "point_system": show.point_system,
        "progressions": show.progressions,
    }


def _draw_entries(cursor, show_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT song.id AS song_id, song.country_id, country.name AS country_name,
               song.title, song.native_title, song.artist, song.entry_number,
               song_show.running_order
        FROM song_show
        JOIN current_song AS song ON song.id = song_show.song_id
        JOIN country ON country.id = song.country_id
        WHERE song_show.show_id = %s
        ORDER BY song_show.running_order, song_show.id
        """,
        (show_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _result_entries(cursor, show_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT country_show_results.song_id, country_show_results.country_id,
               country_show_results.country_name, song.title, song.native_title,
               song.artist, song.entry_number, country_show_results.running_order,
               country_show_results.place, country_show_results.total_points,
               country_show_results.total_votes_received,
               country_show_results.point_distribution,
               country_show_results.total_countries,
               country_show_results.placement_percentage,
               country_show_results.max_possible_points,
               country_show_results.points_percentage,
               country_show_results.adjusted_max_possible_points,
               country_show_results.points_midpoint,
               country_show_results.adjusted_points_percentage,
               country_show_results.entry_status,
               country_show_results.special_qualifier,
               country_show_results.max_pts, country_show_results.total_voters,
               COALESCE(song_show.penalty, 0) AS penalty
        FROM country_show_results
        JOIN current_song AS song ON song.id = country_show_results.song_id
        LEFT JOIN song_show
          ON song_show.song_id = country_show_results.song_id
         AND song_show.show_id = country_show_results.show_id
        WHERE country_show_results.show_id = %s
          AND country_show_results.result_mode = 'official'
        ORDER BY country_show_results.place, country_show_results.song_id
        """,
        (show_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _qualifiers(cursor, entries: list[dict], show) -> list[dict]:
    cursor.execute(
        """
        SELECT song_id, target_show_id, qualifier_order, is_special
        FROM show_qualifier
        WHERE source_show_id = %s
        """,
        (show.id,),
    )
    saved = {row["song_id"]: row for row in cursor.fetchall()}
    result = []
    for progression in show.progressions:
        candidates = []
        for fallback_order, entry in enumerate(entries, 1):
            saved_entry = saved.get(entry["song_id"])
            belongs_here = (
                saved_entry["target_show_id"] == progression["target_show_id"]
                if saved_entry
                else entry["entry_status"] == progression["target_short_name"]
            )
            if belongs_here:
                candidates.append(
                    (
                        saved_entry["qualifier_order"]
                        if saved_entry
                        else fallback_order,
                        fallback_order,
                        entry,
                        saved_entry,
                    )
                )
        for _order, _fallback, entry, saved_entry in sorted(candidates):
            result.append(
                {
                    "song_id": entry["song_id"],
                    "country_id": entry["country_id"],
                    "target_show_id": progression["target_show_id"],
                    "target_short_name": progression["target_short_name"],
                    "special": (
                        saved_entry["is_special"]
                        if saved_entry
                        else entry["special_qualifier"]
                    ),
                }
            )
    return result


@bp.get("/<show>")
def results(show: str):
    show_data, year_status, error = _show_and_year(show)
    if error:
        return error
    permissions = _restricted_permissions()
    access, error = _public_access(show_data, year_status, permissions)
    if error:
        return error

    cursor = get_db().cursor()
    if access == "draw":
        entries = _draw_entries(cursor, show_data.id)
        return resp(
            {
                "show": _show_json(show_data, show, year_status),
                "access": access,
                "entries": entries,
                "qualifiers": [],
                "voter_count": None,
            }
        )

    entries = _result_entries(cursor, show_data.id)
    cursor.execute(
        "SELECT COUNT(*) AS count FROM vote_set WHERE show_id = %s AND result_mode = 'official'",
        (show_data.id,),
    )
    voter_count = cursor.fetchone()["count"]
    qualifiers = (
        _qualifiers(cursor, entries, show_data) if show_data.status == "partial" else []
    )

    if access == "partial":
        qualifier_ids = {entry["song_id"] for entry in qualifiers}
        entries = [entry for entry in entries if entry["song_id"] not in qualifier_ids]

    return resp(
        {
            "show": _show_json(show_data, show, year_status),
            "access": access,
            "entries": entries,
            "qualifiers": qualifiers,
            "voter_count": voter_count,
        }
    )


@bp.get("/<show>/detailed")
def detailed_results(show: str):
    show_data, year_status, error = _show_and_year(show)
    if error:
        return error
    permissions = _restricted_permissions()
    access, error = _public_access(show_data, year_status, permissions)
    if error:
        return error
    if access != "full":
        return err(ErrorID.FORBIDDEN, "Detailed results are only available for a full show")
    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return err(ErrorID.FORBIDDEN, "Voting has not closed yet")

    cursor = get_db().cursor()
    entries = _result_entries(cursor, show_data.id)
    cursor.execute(
        """
        SELECT vote_set.id, account.username, vote_set.nickname,
               vote_set.country_id, country.name AS country_name, vote_set.created_at
        FROM vote_set
        JOIN account ON account.id = vote_set.voter_id
        LEFT JOIN country ON country.id = vote_set.country_id
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
        ORDER BY vote_set.created_at, vote_set.id
        """,
        (show_data.id,),
    )
    voters = [dict(row) for row in cursor.fetchall()]
    voter_ids = [voter.pop("id") for voter in voters]
    votes_by_set: dict[int, list[dict]] = {voter_id: [] for voter_id in voter_ids}
    if voter_ids:
        cursor.execute(
            """
            SELECT vote_set_id, song_id, score
            FROM vote
            WHERE vote_set_id = ANY(%s)
            ORDER BY vote_set_id, score DESC
            """,
            (voter_ids,),
        )
        for vote in cursor.fetchall():
            votes_by_set[vote["vote_set_id"]].append(
                {"song_id": vote["song_id"], "score": vote["score"]}
            )
    for voter, voter_id in zip(voters, voter_ids, strict=True):
        voter["votes"] = votes_by_set[voter_id]

    return resp(
        {
            "show": _show_json(show_data, show, year_status),
            "entries": entries,
            "voters": voters,
        }
    )
