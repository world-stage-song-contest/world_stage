"""Site-wide scrobbling endpoints for catalog-song playback."""

import time

from flask import Blueprint, request

from .. import scrobble
from ..db import get_db
from ..utils import get_user_id_from_session

bp = Blueprint("scrobble", __name__, url_prefix="/scrobble")

# Playback submissions are made while the page is still alive.  Allow a
# generous window for long songs and suspended mobile tabs, but reject bogus
# timestamps that Last.fm would interpret as unrelated historical plays.
SCROBBLE_MAX_AGE = 24 * 60 * 60
SCROBBLE_FUTURE_TOLERANCE = 5 * 60


def _user_id() -> int | None:
    user = get_user_id_from_session(request.cookies.get("session"))
    return user[0] if user else None


def _catalog_song(song_id) -> dict | None:
    """Resolve public playback metadata from the authoritative catalog."""
    if not isinstance(song_id, int) or isinstance(song_id, bool):
        return None

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, title, artist, duration
        FROM current_song
        WHERE id = %s
        """,
        (song_id,),
    )
    return cursor.fetchone()


def _submission() -> tuple[int, dict, dict] | None:
    user_id = _user_id()
    if user_id is None:
        return None
    data = request.get_json(silent=True) or {}
    song = _catalog_song(data.get("song_id"))
    if song is None:
        return None
    return user_id, song, data


@bp.post("/now-playing")
def now_playing():
    submission = _submission()
    if submission is None:
        return "", 204
    user_id, song, _data = submission
    scrobble.send_to_all(
        user_id,
        artist=song["artist"],
        track=song["title"],
        duration=song["duration"],
        album=None,
    )
    return "", 204


@bp.post("")
def submit_scrobble():
    submission = _submission()
    if submission is None:
        return "", 204
    user_id, song, data = submission

    timestamp = data.get("timestamp")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return "", 204
    now = int(time.time())
    if timestamp < now - SCROBBLE_MAX_AGE or timestamp > now + SCROBBLE_FUTURE_TOLERANCE:
        return "", 204

    scrobble.send_to_all(
        user_id,
        artist=song["artist"],
        track=song["title"],
        timestamp=timestamp,
        duration=song["duration"],
        album=None,
    )
    return "", 204
