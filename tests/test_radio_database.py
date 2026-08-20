"""Properties of the persisted radio schedule."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _add_radio_songs(cursor, durations):
    song_ids = []
    for entry_number, duration in enumerate(durations, start=1):
        cursor.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               VALUES ('US', 2024, %s) RETURNING id""",
            (entry_number,),
        )
        song_id = cursor.fetchone()["id"]
        song_ids.append(song_id)
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id,
                   video_link, duration
               ) VALUES (%s, 1, %s, test_artist_credit('Artist'), %s, %s)""",
            (
                song_id,
                f"Song {entry_number}",
                f"https://media.world-stage.org/song-{entry_number}.mp4",
                duration,
            ),
        )
    return song_ids


def _aware_datetimes():
    return st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2035, 12, 31, 23, 59, 59),
    ).map(lambda value: value.replace(tzinfo=UTC))


def test_media_type_depends_on_the_path_extension_not_case_or_url_suffix(db):
    known_extensions = {
        "mp4": "video/mp4",
        "m4v": "video/mp4",
        "m4a": "audio/mp4",
        "webm": "video/webm",
        "ogg": "video/ogg",
        "mov": "video/mp4",
    }

    @given(
        extension=st.sampled_from([*known_extensions, "html", "bin", "unknown"]),
        uppercase=st.booleans(),
        suffix=st.sampled_from(["", "?download=1", "#player"]),
    )
    def property_test(extension, uppercase, suffix):
        rendered_extension = extension.upper() if uppercase else extension
        row = db.execute(
            "SELECT radio_media_type(%s) AS media_type",
            (f"https://example.test/song.{rendered_extension}{suffix}",),
        ).fetchone()

        assert row["media_type"] == known_extensions.get(extension)

    property_test()


def test_refill_rejects_every_nonpositive_capacity_argument(db, isolated_example):
    @given(
        invalid_field=st.sampled_from(["minimum", "batch_size"]),
        invalid_value=st.integers(min_value=-32_768, max_value=0),
    )
    def property_test(invalid_field, invalid_value):
        minimum = invalid_value if invalid_field == "minimum" else 10
        batch_size = invalid_value if invalid_field == "batch_size" else 50
        with isolated_example(), pytest.raises(psycopg.errors.RaiseException):
            db.execute(
                "SELECT refill_radio_queue(%s, %s, %s)",
                (datetime(2026, 1, 1, tzinfo=UTC), minimum, batch_size),
            )

    property_test()


def test_generated_schedules_are_gapless_complete_and_deterministic_snapshots(db, isolated_example):
    @settings(max_examples=30, deadline=None)
    @given(
        durations=st.lists(
            st.integers(min_value=1, max_value=600),
            min_size=1,
            max_size=8,
        ),
        at=_aware_datetimes(),
    )
    def property_test(durations, at):
        with isolated_example(), db.cursor() as cursor:
            song_ids = _add_radio_songs(cursor, durations)
            cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (at,))
            current = cursor.fetchone()
            cursor.execute(
                """SELECT source_song_id, starts_at, ends_at,
                          media_duration_seconds,
                          lag(ends_at) OVER (ORDER BY starts_at) AS previous_end
                   FROM radio_slot ORDER BY starts_at"""
            )
            slots = cursor.fetchall()

            assert current["starts_at"] == at
            assert current["server_time"] == at
            assert len(slots) == len(durations)
            assert {slot["source_song_id"] for slot in slots} == set(song_ids)
            assert all(
                (slot["ends_at"] - slot["starts_at"]).total_seconds()
                == pytest.approx(slot["media_duration_seconds"])
                for slot in slots
            )
            assert all(slot["starts_at"] == slot["previous_end"] for slot in slots[1:])

            after_expiry = slots[-1]["ends_at"] + timedelta(seconds=1)
            cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (after_expiry,))
            restarted = cursor.fetchone()
            assert restarted["starts_at"] == after_expiry

    property_test()


def test_scheduled_metadata_survives_later_catalog_changes(db, isolated_example):
    from world_stage.utils.song_revisions import create_song_revision, withdraw_song

    @settings(max_examples=20, deadline=None)
    @given(
        duration=st.integers(min_value=1, max_value=600),
        replacement_duration=st.integers(min_value=1, max_value=1_000),
        replacement_title=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
            min_size=1,
            max_size=50,
        ),
    )
    def property_test(duration, replacement_duration, replacement_title):
        with isolated_example(), db.cursor() as cursor:
            song_id = _add_radio_songs(cursor, [duration])[0]
            cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (datetime.now(UTC),))
            scheduled = cursor.fetchone()
            create_song_revision(
                cursor,
                song_id,
                {
                    "title": replacement_title,
                    "artist": "Changed",
                    "duration": replacement_duration,
                },
                changed_by=None,
            )
            withdraw_song(cursor, song_id, changed_by=None)
            cursor.execute("SELECT * FROM radio_slot WHERE id = %s", (scheduled["slot_id"],))
            persisted = cursor.fetchone()

            for field in (
                "title",
                "artist",
                "media_url",
                "media_type",
                "media_duration_seconds",
            ):
                assert persisted[field] == scheduled[field]

    property_test()


def test_concurrent_refills_create_only_one_batch(db, _seeded_db):
    with db.cursor() as cursor:
        _add_radio_songs(cursor, [90 + index for index in range(60)])
    db.commit()

    @settings(max_examples=5, deadline=None)
    @given(at=_aware_datetimes())
    def property_test(at):
        db.execute("DELETE FROM radio_slot")
        db.commit()

        def refill():
            connection = psycopg.connect(_seeded_db)
            try:
                row = connection.execute("SELECT refill_radio_queue(%s)", (at,)).fetchone()
                connection.commit()
                return row[0]
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            inserted = list(executor.map(lambda _: refill(), range(2)))

        assert sorted(inserted) == [0, 50]
        assert db.execute("SELECT count(*) AS count FROM radio_slot").fetchone()["count"] == 50

    property_test()
