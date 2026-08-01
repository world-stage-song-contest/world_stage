"""Database-level tests for the persisted radio schedule."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

SCHEDULE_START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_radio_slots(db):
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM radio_slot")
    db.commit()
    yield
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM radio_slot")
    db.commit()


def _add_radio_songs(db):
    with db.cursor() as cursor:
        for country, title, link, duration in [
            ("US", "Song US", "https://media.world-stage.org/us.mp4", 100.0),
            ("ES", "Song ES", "https://media.world-stage.org/es.m4a", 200.0),
            ("FR", "Song FR", "https://media.world-stage.org/fr.mov", 301.0),
        ]:
            cursor.execute(
                """INSERT INTO song (country_id, year_id, entry_number)
                   VALUES (%s, 2024, 1) RETURNING id""",
                (country,),
            )
            cursor.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist, video_link,
                       duration
                   ) VALUES (%s, 1, %s, 'Artist', %s, %s)""",
                (cursor.fetchone()["id"], title, link, duration),
            )
    db.commit()


def _add_many_radio_songs(db, count=60):
    with db.cursor() as cursor:
        for entry_number in range(1, count + 1):
            cursor.execute(
                """INSERT INTO song (country_id, year_id, entry_number)
                   VALUES ('US', 2024, %s) RETURNING id""",
                (entry_number,),
            )
            cursor.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist, video_link,
                       duration
                   ) VALUES (%s, 1, %s, 'Artist', %s, %s)""",
                (
                    cursor.fetchone()["id"], f"Song {entry_number}",
                    f"https://media.world-stage.org/song-{entry_number}.mp4",
                    float(90 + entry_number),
                ),
            )
    db.commit()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://media.world-stage.org/song.mp4", "video/mp4"),
        ("https://media.world-stage.org/song.M4V?download=1", "video/mp4"),
        ("https://media.world-stage.org/song.m4a#player", "audio/mp4"),
        ("https://media.world-stage.org/song.webm", "video/webm"),
        ("https://media.world-stage.org/song.ogg", "video/ogg"),
        ("https://media.world-stage.org/song.mov", "video/mp4"),
        ("https://example.com/watch?v=123", None),
    ],
)
def test_radio_media_type(db, url, expected):
    with db.cursor() as cursor:
        cursor.execute("SELECT radio_media_type(%s) AS media_type", (url,))
        row = cursor.fetchone()
    assert row is not None
    assert row["media_type"] == expected


def test_empty_pool_does_not_create_a_slot(db):
    with db.cursor() as cursor:
        cursor.execute("SELECT refill_radio_queue(%s) AS inserted", (SCHEDULE_START,))
        refill = cursor.fetchone()
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (SCHEDULE_START,))
        current = cursor.fetchone()
    db.commit()

    assert refill is not None
    assert refill["inserted"] == 0
    assert current is None


@pytest.mark.parametrize(
    ("at", "minimum", "batch_size", "message"),
    [
        (None, 10, 50, "p_at must not be null"),
        (SCHEDULE_START, 0, 50, "p_minimum_remaining must be positive"),
        (SCHEDULE_START, 10, 0, "p_batch_size must be positive"),
    ],
)
def test_refill_rejects_invalid_arguments(db, at, minimum, batch_size, message):
    with pytest.raises(psycopg.errors.RaiseException, match=message), db.cursor() as cursor:
        cursor.execute(
            "SELECT refill_radio_queue(%s, %s, %s)",
            (at, minimum, batch_size),
        )
    db.rollback()


def test_get_current_radio_slot_builds_gapless_snapshot_batch(db):
    _add_radio_songs(db)
    at = SCHEDULE_START

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (at,))
        current = cursor.fetchone()
    db.commit()

    assert current is not None
    assert current["server_time"] == at
    assert current["starts_at"] == at
    assert current["pool_size_at_queue"] == 3

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                source_song_id,
                starts_at,
                ends_at,
                media_duration_seconds,
                lag(ends_at) OVER (ORDER BY starts_at) AS previous_end
            FROM radio_slot
            ORDER BY starts_at
            """
        )
        slots = cursor.fetchall()

        cursor.execute("SELECT refill_radio_queue(%s, 1, 50) AS inserted", (at,))
        refill = cursor.fetchone()

    assert len(slots) == 3
    assert len({slot["source_song_id"] for slot in slots}) == 3
    assert slots[0]["starts_at"] == at
    assert all(
        (slot["ends_at"] - slot["starts_at"]).total_seconds()
        == pytest.approx(slot["media_duration_seconds"])
        for slot in slots
    )
    assert all(
        slot["starts_at"] == slot["previous_end"]
        for slot in slots[1:]
    )
    assert refill is not None
    assert refill["inserted"] == 0


def test_refill_uses_ten_slot_low_watermark_and_fifty_song_batch(db):
    _add_many_radio_songs(db)
    at = SCHEDULE_START

    with db.cursor() as cursor:
        cursor.execute("SELECT refill_radio_queue(%s) AS inserted", (at,))
        initial_refill = cursor.fetchone()
    db.commit()
    assert initial_refill is not None
    assert initial_refill["inserted"] == 50

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, source_song_id, starts_at, ends_at
            FROM radio_slot
            ORDER BY starts_at
            """
        )
        initial_slots = cursor.fetchall()

    assert len(initial_slots) == 50
    assert len({slot["source_song_id"] for slot in initial_slots}) == 50

    ten_remaining_at = initial_slots[39]["starts_at"] + (
        initial_slots[39]["ends_at"] - initial_slots[39]["starts_at"]
    ) / 2
    nine_remaining_at = initial_slots[40]["starts_at"] + (
        initial_slots[40]["ends_at"] - initial_slots[40]["starts_at"]
    ) / 2

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT refill_radio_queue(%s) AS inserted",
            (ten_remaining_at,),
        )
        at_threshold = cursor.fetchone()
        cursor.execute(
            "SELECT refill_radio_queue(%s) AS inserted",
            (nine_remaining_at,),
        )
        below_threshold = cursor.fetchone()
    db.commit()

    assert at_threshold is not None
    assert at_threshold["inserted"] == 0
    assert below_threshold is not None
    assert below_threshold["inserted"] == 50

    last_initial_id = max(slot["id"] for slot in initial_slots)
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_song_id
            FROM radio_slot
            WHERE id > %s
            """,
            (last_initial_id,),
        )
        appended_ids = [row["source_song_id"] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT count(*) AS count FROM radio_slot WHERE starts_at >= %s",
            (nine_remaining_at,),
        )
        remaining = cursor.fetchone()

    assert len(appended_ids) == 50
    assert len(set(appended_ids)) == 50
    assert remaining is not None
    assert remaining["count"] == 59


def test_refill_restarts_at_requested_time_after_queue_expires(db):
    _add_radio_songs(db)
    first_at = SCHEDULE_START
    restarted_at = first_at + timedelta(days=1)

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (first_at,))
        first = cursor.fetchone()
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (restarted_at,))
        restarted = cursor.fetchone()
    db.commit()

    assert first is not None
    assert restarted is not None
    assert first["ends_at"] < restarted_at
    assert restarted["starts_at"] == restarted_at


def test_slots_are_not_truncated_at_midnight(db):
    _add_radio_songs(db)
    at = datetime(2026, 1, 1, 23, 59, 30, tzinfo=UTC)

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (at,))
        slot = cursor.fetchone()
    db.commit()

    assert slot is not None
    starts_at_utc = slot["starts_at"].astimezone(UTC)
    ends_at_utc = slot["ends_at"].astimezone(UTC)
    assert starts_at_utc.date() == at.date()
    assert ends_at_utc.date() == (at + timedelta(days=1)).date()
    assert (slot["ends_at"] - slot["starts_at"]).total_seconds() == pytest.approx(
        slot["media_duration_seconds"]
    )


def test_slot_snapshot_survives_catalog_update_and_deletion(db):
    _add_radio_songs(db)
    at = datetime.now(UTC)

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (at,))
        scheduled = cursor.fetchone()
    db.commit()
    assert scheduled is not None

    with db.cursor() as cursor:
        from world_stage.utils.song_revisions import create_song_revision, withdraw_song
        create_song_revision(
            cursor, scheduled["source_song_id"],
            {"title": "Changed", "artist": "Changed", "duration": 999},
            changed_by=None,
        )
        withdraw_song(cursor, scheduled["source_song_id"], changed_by=None)
    db.commit()

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM radio_slot WHERE id = %s", (scheduled["slot_id"],))
        persisted = cursor.fetchone()

    assert persisted is not None
    assert persisted["title"] == scheduled["title"]
    assert persisted["artist"] == scheduled["artist"]
    assert persisted["media_url"] == scheduled["media_url"]
    assert persisted["media_type"] == scheduled["media_type"]
    assert persisted["media_duration_seconds"] == scheduled["media_duration_seconds"]


def test_radio_queue_view_exposes_playing_and_queued_slots(db):
    _add_radio_songs(db)
    at = datetime.now(UTC)

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (at,))
        assert cursor.fetchone() is not None
    db.commit()

    with db.cursor() as cursor:
        cursor.execute("SELECT status FROM radio_queue ORDER BY starts_at")
        statuses = [row["status"] for row in cursor.fetchall()]

    assert statuses == ["playing", "queued", "queued"]


def test_concurrent_refills_append_only_one_batch(db, _seeded_db):
    _add_many_radio_songs(db)
    at = SCHEDULE_START

    def refill():
        connection = psycopg.connect(_seeded_db)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT refill_radio_queue(%s)", (at,))
                row = cursor.fetchone()
            connection.commit()
            assert row is not None
            return row[0]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        inserted = list(executor.map(lambda _: refill(), range(2)))

    assert sorted(inserted) == [0, 50]
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS count FROM radio_slot")
        count = cursor.fetchone()
    assert count is not None
    assert count["count"] == 50
