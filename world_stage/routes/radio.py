import time

from flask import Blueprint, jsonify

from ..db import get_db
from ..utils import LCG, render_template
from .country import mime_types

bp = Blueprint("radio", __name__, url_prefix="/radio")

DAY_SECONDS = 86400


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


def _now_playing() -> dict | None:
    """Compute what the radio is playing at this instant.

    Each song is a truly random pick from the whole pool (repeats in
    close proximity are allowed), drawn from an LCG seeded by the UTC
    day number. Songs chain gaplessly: replaying the day's draws and
    summing durations up to the current moment gives the same song and
    offset for everyone computing it. The anchor at UTC midnight is
    what keeps the walk short; the song playing across midnight is cut
    off there, once per day.
    """
    pool = _get_pool()
    if not pool:
        return None

    now = time.time()
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
    year_label = song["special_name"] or str(song["year_id"])
    return {
        "server_time": now,
        "slot_start": slot_start,
        "slot_end": min(slot_start + song["duration"], day_start + DAY_SECONDS),
        "offset": now - slot_start,
        "pool_size": len(pool),
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


@bp.get("/")
def index():
    return render_template("radio.html")


@bp.get("/now")
def now_playing():
    data = _now_playing()
    if data is None:
        return jsonify({"error": "No songs available yet"}), 404
    return jsonify(data)
