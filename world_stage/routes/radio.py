import time

from flask import Blueprint, jsonify, request

from .. import scrobble
from ..db import get_db
from ..utils import LCG, get_user_id_from_session, render_template, with_user
from .country import mime_types

bp = Blueprint("radio", __name__, url_prefix="/radio")

DAY_SECONDS = 86400

# A scrobble submission must name a song whose scheduled slot start is
# within this many seconds of the timestamp the client reports (covers
# clock skew + the handoff window), and no older than the max age.
SCROBBLE_SLOT_TOLERANCE = 5.0
SCROBBLE_MAX_AGE = 3600.0


def _mime(url: str) -> str | None:
    """Closed-year songs are hosted on media.world-stage.org as direct
    media files; anything the browser can't play (or seek into) is
    left out of the rotation."""
    return mime_types.get(url.rsplit(".", 1)[-1].lower())


def _get_pool() -> list[dict]:
    """All playable songs from closed years, in a stable order.

    The order (song.id) must be deterministic: the schedule is a pure
    function of UTC time and this list, which is how every instance
    stays in sync without any shared state.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, song.title, song.artist, song.video_link, song.poster_link,
            song.vtt_link, song.duration, year.id AS year_id, year.special_name,
            country.id AS cc, country.name AS country_name
        FROM song
        JOIN year ON year.id = song.year_id
        JOIN country ON country.id = song.country_id
        WHERE year.status = 'closed'
          AND NOT song.is_placeholder
          AND song.video_link IS NOT NULL
          AND song.duration > 0
        ORDER BY song.id
        """
    )
    pool = []
    for row in cursor.fetchall():
        mime = _mime(row["video_link"])
        if mime:
            row["mime"] = mime
            pool.append(row)
    return pool


def _song_at(now: float, pool: list[dict] | None = None) -> dict | None:
    """The song scheduled at UTC instant ``now``, with its slot timing.

    Each song is a truly random pick from the whole pool (repeats in
    close proximity are allowed), drawn from an LCG seeded by the UTC
    day number. Songs chain gaplessly: replaying the day's draws and
    summing durations up to a given moment gives the same song and
    offset for everyone computing it. The anchor at UTC midnight is
    what keeps the walk short; the song playing across midnight is cut
    off there, once per day.

    Pure function of ``(now, pool)`` — shared by the live ``/radio/now``
    endpoint and the server-side scrobble validator.
    """
    if pool is None:
        pool = _get_pool()
    if not pool:
        return None

    day = int(now // DAY_SECONDS)
    day_start = day * DAY_SECONDS
    elapsed = now - day_start

    rng = LCG(day)
    start = 0.0
    while True:
        song = pool[rng.next(len(pool))]
        end = start + song["duration"]
        if elapsed < end:
            break
        start = end

    slot_start = day_start + start
    return {
        "song": song,
        "slot_start": slot_start,
        "slot_end": min(slot_start + song["duration"], day_start + DAY_SECONDS),
    }


def _now_playing() -> dict | None:
    now = time.time()
    slot = _song_at(now)
    if slot is None:
        return None
    song = slot["song"]
    year_label = song["special_name"] or str(song["year_id"])
    return {
        "server_time": now,
        "slot_start": slot["slot_start"],
        "slot_end": slot["slot_end"],
        "offset": now - slot["slot_start"],
        "pool_size": len(_get_pool()),
        "song": {
            "id": song["id"],
            "title": song["title"],
            "artist": song["artist"],
            "country": song["country_name"],
            "cc": song["cc"].lower(),
            "year_id": song["year_id"],
            "year": year_label,
            "url": song["video_link"],
            "duration": song["duration"],
            "mime": song["mime"],
            "poster": song["poster_link"],
            "vtt": song["vtt_link"],
        },
    }


def _validate_submission(started_at, song_id) -> dict | None:
    """Recompute the authoritative slot at ``started_at`` and confirm it
    matches the claimed song. Returns ``{song, slot_start}`` (the
    server's own metadata) or None — so a client can only ever scrobble
    a song that genuinely was on the radio at a recent, slot-aligned
    moment, never arbitrary tracks."""
    if not isinstance(started_at, (int, float)) or not isinstance(song_id, int):
        return None
    now = time.time()
    if started_at > now + SCROBBLE_SLOT_TOLERANCE:
        return None
    if now - started_at > SCROBBLE_MAX_AGE:
        return None
    slot = _song_at(started_at)
    if slot is None or slot["song"]["id"] != song_id:
        return None
    if abs(started_at - slot["slot_start"]) > SCROBBLE_SLOT_TOLERANCE:
        return None
    return slot


def _scrobble_user():
    """The logged-in user id for a scrobble POST, or None."""
    user = get_user_id_from_session(request.cookies.get("session"))
    return user[0] if user else None


def _submission_song():
    """Validate a scrobble/now-playing POST body and return the
    server's authoritative slot, or None to reject."""
    data = request.get_json(silent=True) or {}
    return _validate_submission(data.get("started_at"), data.get("song_id"))


@bp.get("/")
@with_user
def index(user: tuple[int, str] | None):
    enabled = bool(user) and scrobble.has_enabled_account(user[0])
    return render_template("radio.html", scrobble_enabled=enabled)


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
