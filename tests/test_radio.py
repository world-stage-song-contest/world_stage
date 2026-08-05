"""Tests for the database-backed /radio endpoints."""

from world_stage import create_app
from world_stage.utils.song_revisions import set_song_status


def _add_song(
    db,
    cc,
    year,
    title,
    link,
    *,
    duration=None,
    placeholder=False,
    entry=1,
    poster=None,
    vtt=None,
):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, entry_number)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (cc, year, entry),
        )
        song_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist, video_link, duration,
                   poster_link, vtt_link
               ) VALUES (%s, 1, %s, 'Artist', %s, %s, %s, %s)""",
            (song_id, title, link, duration, poster, vtt),
        )
        set_song_status(cur, song_id, changed_by=1, is_placeholder=placeholder)
    db.commit()


def _media(name):
    return f"https://media.world-stage.org/{name}"


def _add_three_songs(db):
    """Add three playable songs with distinct durations."""
    for cc, title, duration in [
        ("US", "Song US", 100.0),
        ("ES", "Song ES", 200.0),
        ("FR", "Song FR", 301.0),
    ]:
        _add_song(db, cc, 2024, title, _media(f"ws2024{cc.lower()}.mp4"), duration=duration)


class TestRadioNow:
    def test_404_when_no_songs(self, client):
        resp = client.get("/radio/now")
        assert resp.status_code == 404

    def test_pool_only_has_playable_closed_year_songs(self, client, db):
        _add_song(db, "US", 2024, "Good", _media("ws2024us.mov"), duration=180.0,
                  poster=_media("ws2024us.png"), vtt=_media("ws2024us.vtt"))
        _add_song(db, "ES", 2024, "Placeholder", _media("ws2024es.mp4"),
                  duration=180.0, placeholder=True)
        _add_song(db, "FR", 2024, "No duration", _media("ws2024fr.mp4"))
        _add_song(db, "US", 2025, "Open year", _media("ws2025us.mp4"), duration=180.0)

        resp = client.get("/radio/now")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data) == {
            "offset",
            "pool_size",
            "server_time",
            "slot_end",
            "slot_id",
            "slot_start",
            "song",
        }
        assert set(data["song"]) == {
            "artist",
            "cc",
            "country",
            "duration",
            "id",
            "mime",
            "poster",
            "title",
            "url",
            "vtt",
            "year",
            "year_id",
        }
        assert isinstance(data["slot_id"], int)
        assert data["pool_size"] == 1
        assert data["song"]["title"] == "Good"
        assert data["song"]["url"] == _media("ws2024us.mov")
        assert data["song"]["mime"] == "video/mp4"
        assert data["song"]["poster"] == _media("ws2024us.png")
        assert data["song"]["vtt"] == _media("ws2024us.vtt")
        assert data["song"]["country"] == "United States"
        assert data["song"]["year"] == "2024"

    def test_same_window_returns_same_persisted_slot(self, client, db):
        _add_three_songs(db)

        first = client.get("/radio/now").get_json()
        second = client.get("/radio/now").get_json()

        assert first["slot_id"] == second["slot_id"]
        assert first["song"]["id"] == second["song"]["id"]
        assert second["offset"] >= first["offset"]
        assert first["slot_start"] <= first["server_time"] < first["slot_end"]
        assert first["slot_end"] - first["slot_start"] == first["song"]["duration"]

    def test_current_slot_does_not_change_when_pool_changes(self, client, db):
        _add_song(db, "US", 2024, "Original", _media("ws2024us.mp4"), duration=180.0)
        first = client.get("/radio/now").get_json()

        _add_song(db, "ES", 2024, "New", _media("ws2024es.mp4"), duration=180.0)
        second = client.get("/radio/now").get_json()

        assert second["slot_id"] == first["slot_id"]
        assert second["song"] == first["song"]
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) AS count
                FROM radio_slot
                WHERE title = 'New' AND starts_at > clock_timestamp()
                """
            )
            queued_new_song = cursor.fetchone()
        assert queued_new_song is not None
        assert queued_new_song["count"] == 1

    def test_synchronised_across_instances(self, client, db, _seeded_db):
        _add_three_songs(db)

        other_app = create_app(
            {"TESTING": True, "LOCAL_ASSETS": True, "DATABASE_URI": _seeded_db}
        )
        other_client = other_app.test_client()

        first = client.get("/radio/now").get_json()
        second = other_client.get("/radio/now").get_json()
        assert first["slot_id"] == second["slot_id"]
        assert first["song"] == second["song"]
        assert abs(first["offset"] - second["offset"]) < 1


def test_radio_client_posts_and_deduplicates_by_slot_id(client):
    resp = client.get("/static/js/radio.js")
    assert resp.status_code == 200
    source = resp.get_data(as_text=True)

    assert "JSON.stringify({ slot_id: slot.slot_id })" in source
    assert "slot.slot_id === scrobbledSlotId" in source
    assert "data.slot_id === current.slot_id" in source
