"""Behavioral properties for the database-backed radio endpoint."""

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage import create_app
from world_stage.utils.song_revisions import set_song_status


def _add_song(
    db,
    country,
    year,
    title,
    *,
    duration,
    placeholder=False,
):
    with db.cursor() as cursor:
        song_id = cursor.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               SELECT %s, %s, COALESCE(MAX(entry_number), 0) + 1
               FROM song WHERE country_id = %s AND year_id = %s
               RETURNING id""",
            (country, year, country, year),
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id,
                   video_link, duration
               ) VALUES (
                   %s, 1, %s, test_artist_credit('Artist'),
                   %s, %s
               )""",
            (
                song_id,
                title,
                f"https://media.world-stage.org/radio-{song_id}.mp4",
                duration,
            ),
        )
        set_song_status(cursor, song_id, changed_by=1, is_placeholder=placeholder)
    db.commit()
    return song_id


def _clear_songs(db, song_ids):
    db.rollback()
    db.execute("DELETE FROM song_status WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song_data WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song WHERE id = ANY(%s)", (song_ids,))
    db.commit()


def _clear_radio_example(db, song_ids):
    db.execute("DELETE FROM radio_slot")
    db.commit()
    _clear_songs(db, song_ids)


def test_radio_pool_contains_exactly_playable_non_placeholder_closed_year_entries(client, db):
    @settings(max_examples=12, deadline=None)
    @given(
        closed_year=st.booleans(),
        placeholder=st.booleans(),
        has_duration=st.booleans(),
        duration=st.integers(min_value=1, max_value=600),
    )
    def property_test(closed_year, placeholder, has_duration, duration):
        song_id = _add_song(
            db,
            "US",
            2024 if closed_year else 2025,
            "Generated radio entry",
            duration=duration if has_duration else None,
            placeholder=placeholder,
        )
        try:
            response = client.get("/radio/now")
            eligible = closed_year and not placeholder and has_duration

            assert response.status_code == (200 if eligible else 404)
            if eligible:
                data = response.get_json()
                assert data["pool_size"] == 1
                assert data["song"]["id"] == song_id
                assert data["slot_start"] <= data["server_time"] < data["slot_end"]
                assert data["slot_end"] - data["slot_start"] == duration
        finally:
            _clear_radio_example(db, [song_id])

    property_test()


def test_current_radio_slot_survives_catalog_and_app_instance_changes(client, db, _seeded_db):
    for country, duration in (("US", 100), ("ES", 200), ("FR", 301)):
        _add_song(db, country, 2024, f"Song {country}", duration=duration)
    other_client = create_app(
        {"TESTING": True, "LOCAL_ASSETS": True, "DATABASE_URI": _seeded_db}
    ).test_client()

    @given(
        use_other_instance=st.booleans(),
        catalog_duration=st.one_of(st.none(), st.integers(min_value=1, max_value=600)),
    )
    def property_test(use_other_instance, catalog_duration):
        first = client.get("/radio/now").get_json()
        new_id = None
        try:
            if catalog_duration is not None:
                new_id = _add_song(db, "ES", 2024, "New", duration=catalog_duration)
            selected_client = other_client if use_other_instance else client
            second = selected_client.get("/radio/now").get_json()

            assert second["slot_id"] == first["slot_id"]
            assert second["song"] == first["song"]
            assert second["offset"] >= first["offset"]
        finally:
            if new_id is not None:
                db.execute("DELETE FROM radio_slot WHERE source_song_id = %s", (new_id,))
                db.commit()
                _clear_songs(db, [new_id])

    property_test()
