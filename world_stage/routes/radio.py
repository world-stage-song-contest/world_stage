from datetime import datetime

from flask import Blueprint, jsonify, request

from .. import scrobble
from ..db import get_db
from ..utils import get_user_id_from_session, render_template, with_user

bp = Blueprint("radio", __name__, url_prefix="/radio")

# A scrobble submission must name a song whose scheduled slot start is
# within this many seconds of the timestamp the client reports (covers
# clock skew + the handoff window), and no older than the max age.
SCROBBLE_SLOT_TOLERANCE = 5.0
SCROBBLE_MAX_AGE = 3600.0


def _epoch(value: datetime) -> float:
    return value.timestamp()


def _slot_from_row(row: dict) -> dict:
    """Convert a persisted slot row into the radio's internal shape."""
    return {
        "slot_id": row["slot_id"],
        "slot_start": _epoch(row["starts_at"]),
        "slot_end": _epoch(row["ends_at"]),
        "song": {
            "id": row["source_song_id"],
            "title": row["title"],
            "artist": row["artist"],
            "country": row["country_name"],
            "cc": row["country_code"].lower(),
            "year_id": row["year_id"],
            "year": row["year_label"],
            "url": row["media_url"],
            "duration": row["media_duration_seconds"],
            "mime": row["media_type"],
            "poster": row["poster_url"],
            "vtt": row["vtt_url"],
        },
    }


def _now_playing() -> dict | None:
    """Fetch the current persisted slot, filling the queue if necessary."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM get_current_radio_slot()")
    row = cursor.fetchone()

    # The PostgreSQL function may have appended slots and holds its advisory
    # lock until this transaction ends. Commit before building the response.
    db.commit()

    if row is None:
        return None

    slot = _slot_from_row(row)
    server_time = _epoch(row["server_time"])
    return {
        "server_time": server_time,
        "slot_id": slot["slot_id"],
        "slot_start": slot["slot_start"],
        "slot_end": slot["slot_end"],
        "offset": server_time - slot["slot_start"],
        "pool_size": row["pool_size_at_queue"],
        "song": slot["song"],
    }


_SUBMISSION_SLOT_COLUMNS = """
    id AS slot_id,
    starts_at,
    ends_at,
    source_song_id,
    title,
    artist,
    media_url,
    media_type,
    media_duration_seconds,
    poster_url,
    vtt_url,
    country_code,
    country_name,
    year_id,
    year_label
"""


def _validate_submission(slot_id) -> dict | None:
    """Resolve a recent persisted slot from its authoritative ID."""
    if not isinstance(slot_id, int) or isinstance(slot_id, bool):
        return None

    cursor = get_db().cursor()
    cursor.execute(
        f"""
        SELECT {_SUBMISSION_SLOT_COLUMNS}
        FROM radio_slot
        WHERE id = %s
          AND starts_at <= clock_timestamp() + make_interval(secs => %s)
          AND starts_at >= clock_timestamp() - make_interval(secs => %s)
        """,
        (slot_id, SCROBBLE_SLOT_TOLERANCE, SCROBBLE_MAX_AGE),
    )

    row = cursor.fetchone()
    if row is None:
        return None
    return _slot_from_row(row)


def _scrobble_user():
    """The logged-in user id for a radio scrobble POST, or None."""
    user = get_user_id_from_session(request.cookies.get("session"))
    return user[0] if user else None


def _submission_song():
    """Validate a scrobble/now-playing POST body and return the
    server's authoritative slot, or None to reject."""
    data = request.get_json(silent=True) or {}
    return _validate_submission(data.get("slot_id"))


@bp.get("/")
@with_user
def index(user: tuple[int, str] | None):
    enabled = bool(user) and scrobble.has_enabled_account(user[0])
    return render_template("radio.html", scrobble_enabled=enabled, play_count_enabled=bool(user))


@bp.get("/now")
def now_playing():
    data = _now_playing()
    if data is None:
        return jsonify({"error": "No songs available yet"}), 404
    return jsonify(data)


@bp.post("/now-playing")
def radio_now_playing():
    user_id = _scrobble_user()
    if user_id is None:
        return "", 204
    slot = _submission_song()
    if slot is None:
        return "", 204
    song = slot["song"]
    scrobble.send_to_all(
        user_id, artist=song["artist"], track=song["title"], duration=song["duration"]
    )
    return "", 204


@bp.post("/scrobble")
def radio_scrobble():
    user_id = _scrobble_user()
    if user_id is None:
        return "", 204
    slot = _submission_song()
    if slot is None:
        return "", 204
    song = slot["song"]
    # Use the server's slot_start as the timestamp: two of a user's own
    # tabs scrobbling the same play then submit identical (artist, track,
    # timestamp), which the services dedup.
    scrobble.send_to_all(
        user_id,
        artist=song["artist"],
        track=song["title"],
        timestamp=int(slot["slot_start"]),
        duration=song["duration"],
    )
    return "", 204
