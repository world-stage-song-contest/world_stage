from flask import current_app, url_for
from itsdangerous import BadData, URLSafeSerializer

from .db import fetchone, get_db


def catalog_snapshots(song_ids: list[int]) -> dict[int, dict]:
    if not song_ids:
        return {}
    rows = get_db().execute(
        """
        SELECT song.id AS song_id, song.title, song.artist, song.video_link AS media_url,
               song.duration, country.id AS country_code, country.name AS country_name,
               song.year_id, song.entry_number, year.special_short_name,
               COALESCE((
                   SELECT STRING_AGG(final.name, ', ' ORDER BY final.name, final.id)
                   FROM national_final_song AS membership
                   JOIN national_final AS final ON final.id = membership.national_final_id
                   WHERE membership.song_id = song.id
               ), year.special_name, year.id::text) AS year_label
        FROM current_song AS song
        JOIN country ON country.id = song.country_id
        JOIN year ON year.id = song.year_id
        WHERE song.id = ANY(%s)
        """,
        (song_ids,),
    ).fetchall()
    return {row["song_id"]: row for row in rows}


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="song-play-snapshot")


def snapshot_token(snapshot: dict) -> str:
    return _serializer().dumps(snapshot)


def read_snapshot(token) -> dict | None:
    if not isinstance(token, str):
        return None
    try:
        snapshot = _serializer().loads(token)
    except BadData:
        return None
    return snapshot if isinstance(snapshot, dict) else None


def song_snapshot_token(song_id: int) -> str | None:
    snapshot = catalog_snapshots([song_id]).get(song_id)
    return snapshot_token(snapshot) if snapshot else None


def attach_snapshots(entries: list[dict]) -> None:
    snapshots = catalog_snapshots([entry["id"] for entry in entries if entry["kind"] == "song"])
    for entry in entries:
        if entry["kind"] == "song" and entry["id"] in snapshots:
            entry["play_snapshot"] = snapshot_token(snapshots[entry["id"]])


def radio_snapshot(slot_id) -> dict | None:
    if not isinstance(slot_id, int) or isinstance(slot_id, bool):
        return None
    return get_db().execute(
        """
        SELECT source_song_id AS song_id, title, artist, media_url,
               media_duration_seconds AS duration, country_code, country_name,
               year_id, year_label, NULL::integer AS entry_number,
               NULL::text AS special_short_name
        FROM radio_slot
        WHERE id = %s
          AND starts_at BETWEEN clock_timestamp() - INTERVAL '1 day'
                            AND clock_timestamp() + INTERVAL '5 minutes'
        """,
        (slot_id,),
    ).fetchone()


def record_play(user_id: int, snapshot: dict, timestamp: int) -> int:
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO song_play (
            user_id, song_id, played_at, title, artist, media_url, duration,
            country_code, country_name, year_id, year_label, entry_number, special_short_name
        ) VALUES (
            %(user_id)s, %(song_id)s, to_timestamp(%(timestamp)s), %(title)s, %(artist)s,
            %(media_url)s, %(duration)s, %(country_code)s, %(country_name)s, %(year_id)s,
            %(year_label)s, %(entry_number)s, %(special_short_name)s
        ) RETURNING id
        """,
        {**snapshot, "user_id": user_id, "timestamp": timestamp},
    )
    play_id = fetchone(cursor)["id"]
    db.commit()
    return play_id


def prepare_history(plays: list[dict]) -> None:
    current = catalog_snapshots(list({play["song_id"] for play in plays}))
    for play in plays:
        play["cc"] = play["country_code"].lower()
        play["country"] = play["country_name"]
        play["song_url"] = play["media_url"]
        song = current.get(play["song_id"])
        if song and all(song[key] == play[key] for key in ("title", "artist", "media_url")):
            play["song_url"] = (
                url_for(
                    "country.special_details", code=song["country_code"].lower(),
                    special_short_name=song["special_short_name"],
                    entry_number=song["entry_number"],
                )
                if song["special_short_name"]
                else url_for(
                    "country.details", code=song["country_code"].lower(), year=song["year_id"],
                    entry_number=song["entry_number"],
                )
            )
