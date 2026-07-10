from flask import Blueprint, request

from world_stage.db import fetchone, get_db
from world_stage.utils import (
    ErrorID,
    dt_now,
    err,
    get_countries,
    get_show_id,
    require_api_auth,
    resp,
)

bp = Blueprint("voting", __name__, url_prefix="/voting")


def _show_or_error(key: str):
    show = get_show_id(key)
    if not show or not show.id:
        return None, err(ErrorID.NOT_FOUND, "Show not found")
    return show, None


def _voting_is_open(show) -> bool:
    return not (
        (show.voting_opens and show.voting_opens > dt_now())
        or (show.voting_closes and show.voting_closes < dt_now())
    )


def _predictions_are_open(show) -> bool:
    deadline = show.predictions_close or show.voting_closes
    return not (
        (show.voting_opens and show.voting_opens > dt_now())
        or (deadline and deadline < dt_now())
    )


def _show_json(show, key: str) -> dict:
    return {
        "id": show.id,
        "key": key,
        "year": show.year,
        "name": show.name,
        "short_name": show.short_name,
        "points": show.points,
        "voting_opens": show.voting_opens,
        "voting_closes": show.voting_closes,
        "predictions_close": show.predictions_close,
    }


def _show_songs(cursor, show_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT song.id, song.country_id, country.name AS country_name,
               song.title, song.native_title, song.artist, song.submitter_id,
               song_show.running_order
        FROM song_show
        JOIN song ON song.id = song_show.song_id
        JOIN country ON country.id = song.country_id
        WHERE song_show.show_id = %s
        ORDER BY song_show.running_order, song_show.id
        """,
        (show_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _voter_countries(cursor, user_id: int, year: int) -> list[dict]:
    cursor.execute(
        """
        SELECT DISTINCT country.id, country.name, country.cc3
        FROM song
        JOIN country ON country.id = song.country_id
        WHERE song.submitter_id = %s AND song.year_id = %s
        ORDER BY country.name
        """,
        (user_id, year),
    )
    countries = [dict(row) for row in cursor.fetchall()]
    if countries:
        return countries
    return [
        {"id": country.cc, "name": country.name, "cc3": country.cc3}
        for country in get_countries()
    ]


def _ballot(cursor, user_id: int, show_id: int) -> dict | None:
    cursor.execute(
        """
        SELECT id, nickname, country_id, created_at
        FROM vote_set
        WHERE voter_id = %s AND show_id = %s
        """,
        (user_id, show_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    cursor.execute(
        """
        SELECT score, song_id
        FROM vote
        WHERE vote_set_id = %s
        ORDER BY score DESC
        """,
        (row["id"],),
    )
    return {
        "nickname": row["nickname"],
        "country_id": row["country_id"],
        "created_at": row["created_at"],
        "votes": [dict(vote) for vote in cursor.fetchall()],
    }


def _prediction(cursor, user_id: int, show_id: int) -> dict | None:
    cursor.execute(
        """
        SELECT id, created_at, updated_at
        FROM prediction_set
        WHERE user_id = %s AND show_id = %s
        """,
        (user_id, show_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    cursor.execute(
        "SELECT song_id, position FROM prediction WHERE set_id = %s ORDER BY position",
        (row["id"],),
    )
    return {
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "predictions": [dict(prediction) for prediction in cursor.fetchall()],
    }


def _json_object() -> tuple[dict | None, tuple[dict, int] | None]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, err(ErrorID.BAD_REQUEST, "Expected a JSON object")
    return data, None


def _parse_pairs(
    data: dict, field: str, left: str, right: str
) -> tuple[list[dict] | None, str | None]:
    pairs = data.get(field)
    if not isinstance(pairs, list):
        return None, f"{field} must be a list"
    parsed = []
    for pair in pairs:
        if not isinstance(pair, dict):
            return None, f"Each {field} entry must be an object"
        a, b = pair.get(left), pair.get(right)
        if isinstance(a, bool) or isinstance(b, bool):
            return None, f"Each {field} entry must use integer {left} and {right} values"
        try:
            parsed.append({left: int(a), right: int(b)})
        except (TypeError, ValueError):
            return None, f"Each {field} entry must use integer {left} and {right} values"
    return parsed, None


@bp.get("/<show>")
@require_api_auth
def ballot(show: str, auth):
    show_data, error = _show_or_error(show)
    if error:
        return error
    if not _voting_is_open(show_data):
        return err(ErrorID.BAD_REQUEST, "Voting is closed")

    user_id, _, _ = auth
    cursor = get_db().cursor()
    return resp(
        {
            "show": _show_json(show_data, show),
            "songs": _show_songs(cursor, show_data.id),
            "countries": _voter_countries(cursor, user_id, show_data.year),
            "ballot": _ballot(cursor, user_id, show_data.id),
        }
    )


@bp.put("/<show>")
@require_api_auth
def save_ballot(show: str, auth):
    show_data, error = _show_or_error(show)
    if error:
        return error
    if not _voting_is_open(show_data):
        return err(ErrorID.BAD_REQUEST, "Voting is closed")

    data, error = _json_object()
    if error:
        return error
    votes, message = _parse_pairs(data, "votes", "score", "song_id")
    if message:
        return err(ErrorID.BAD_REQUEST, message)

    user_id, _, _ = auth
    cursor = get_db().cursor()
    songs = _show_songs(cursor, show_data.id)
    song_by_id = {song["id"]: song for song in songs}
    scores = [vote["score"] for vote in votes]
    song_ids = [vote["song_id"] for vote in votes]
    if set(scores) != set(show_data.points) or len(scores) != len(show_data.points):
        return err(ErrorID.BAD_REQUEST, "Votes must contain each show score exactly once")
    if len(song_ids) != len(set(song_ids)):
        return err(ErrorID.BAD_REQUEST, "A song can only receive one score")
    if any(song_id not in song_by_id for song_id in song_ids):
        return err(ErrorID.BAD_REQUEST, "Votes must reference songs in this show")
    if any(song_by_id[song_id]["submitter_id"] == user_id for song_id in song_ids):
        return err(ErrorID.BAD_REQUEST, "You cannot vote for your own song")

    nickname = data.get("nickname")
    if nickname is not None and not isinstance(nickname, str):
        return err(ErrorID.BAD_REQUEST, "nickname must be a string or null")
    nickname = nickname.strip() if nickname else None
    country_id = data.get("country_id")
    if country_id is not None and not isinstance(country_id, str):
        return err(ErrorID.BAD_REQUEST, "country_id must be a string or null")
    country_id = country_id or None

    countries = _voter_countries(cursor, user_id, show_data.year)
    valid_country_ids = {country["id"] for country in countries}
    if country_id and country_id not in valid_country_ids:
        return err(ErrorID.BAD_REQUEST, "country_id is not available to this voter")

    cursor.execute(
        """
        INSERT INTO vote_set (voter_id, show_id, country_id, nickname, ip_address)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (voter_id, show_id) DO UPDATE
        SET country_id = EXCLUDED.country_id,
            nickname = EXCLUDED.nickname,
            ip_address = EXCLUDED.ip_address
        RETURNING id
        """,
        (user_id, show_data.id, country_id, nickname, request.remote_addr),
    )
    vote_set_id = fetchone(cursor)["id"]
    cursor.execute("DELETE FROM vote WHERE vote_set_id = %s", (vote_set_id,))
    cursor.executemany(
        """
        INSERT INTO vote (vote_set_id, song_id, score)
        VALUES (%(set_id)s, %(song_id)s, %(score)s)
        """,
        [{"set_id": vote_set_id, **vote} for vote in votes],
    )
    get_db().commit()
    return resp(_ballot(cursor, user_id, show_data.id))


@bp.get("/<show>/prediction")
@require_api_auth
def prediction(show: str, auth):
    show_data, error = _show_or_error(show)
    if error:
        return error
    if not _predictions_are_open(show_data):
        return err(ErrorID.BAD_REQUEST, "Predictions are closed for this show")

    user_id, _, _ = auth
    cursor = get_db().cursor()
    songs = _show_songs(cursor, show_data.id)
    if not songs:
        return err(ErrorID.NOT_FOUND, "No songs found for this show")
    cursor.execute(
        "SELECT COUNT(*) AS count FROM prediction_set WHERE show_id = %s", (show_data.id,)
    )
    return resp(
        {
            "show": _show_json(show_data, show),
            "songs": songs,
            "prediction_count": fetchone(cursor)["count"],
            "prediction": _prediction(cursor, user_id, show_data.id),
        }
    )


@bp.put("/<show>/prediction")
@require_api_auth
def save_prediction(show: str, auth):
    show_data, error = _show_or_error(show)
    if error:
        return error
    if not _predictions_are_open(show_data):
        return err(ErrorID.BAD_REQUEST, "Predictions are closed for this show")

    data, error = _json_object()
    if error:
        return error
    predictions, message = _parse_pairs(data, "predictions", "song_id", "position")
    if message:
        return err(ErrorID.BAD_REQUEST, message)

    user_id, _, _ = auth
    cursor = get_db().cursor()
    songs = _show_songs(cursor, show_data.id)
    song_ids = {song["id"] for song in songs}
    if not songs:
        return err(ErrorID.NOT_FOUND, "No songs found for this show")
    submitted_song_ids = [prediction["song_id"] for prediction in predictions]
    positions = [prediction["position"] for prediction in predictions]
    expected_positions = set(range(1, len(songs) + 1))
    if set(submitted_song_ids) != song_ids or len(submitted_song_ids) != len(songs):
        return err(ErrorID.BAD_REQUEST, "Predictions must rank every song exactly once")
    if set(positions) != expected_positions or len(positions) != len(songs):
        return err(ErrorID.BAD_REQUEST, "Predictions must use every position exactly once")

    cursor.execute(
        """
        INSERT INTO prediction_set (user_id, show_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, show_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (user_id, show_data.id),
    )
    set_id = fetchone(cursor)["id"]
    cursor.execute("DELETE FROM prediction WHERE set_id = %s", (set_id,))
    cursor.executemany(
        """
        INSERT INTO prediction (set_id, song_id, position)
        VALUES (%(set_id)s, %(song_id)s, %(position)s)
        """,
        [{"set_id": set_id, **prediction} for prediction in predictions],
    )
    get_db().commit()
    return resp(_prediction(cursor, user_id, show_data.id))
