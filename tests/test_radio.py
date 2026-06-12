"""Tests for the /radio endpoints and the gapless duration schedule."""

from world_stage import create_app


def _add_song(db, cc, year, title, link, *, duration=None, placeholder=False,
              entry=1, poster=None, vtt=None):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO song (submitter_id, country_id, year_id, title, artist,
                video_link, duration, is_placeholder, entry_number, poster_link, vtt_link)
            VALUES (1, %s, %s, %s, 'Artist', %s, %s, %s, %s, %s, %s)
            """,
            (cc, year, title, link, duration, placeholder, entry, poster, vtt),
        )
    db.commit()


def _media(name):
    return f"https://media.world-stage.org/{name}"


def _freeze_time(monkeypatch, t):
    monkeypatch.setattr("world_stage.routes.radio.time.time", lambda: float(t))


def _add_three_songs(db):
    """Three songs with distinct durations. 301 is deliberately not a
    multiple of 100 so a day's windows can't tile 86400 exactly and the
    midnight-truncation test has something to truncate."""
    for cc, title, duration in [("US", "Song US", 100.0), ("ES", "Song ES", 200.0),
                                ("FR", "Song FR", 301.0)]:
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
        assert data["pool_size"] == 1
        assert data["song"]["title"] == "Good"
        assert data["song"]["url"] == _media("ws2024us.mov")
        assert data["song"]["mime"] == "video/mp4"
        assert data["song"]["poster"] == _media("ws2024us.png")
        assert data["song"]["vtt"] == _media("ws2024us.vtt")
        assert data["song"]["country"] == "United States"
        assert data["song"]["year"] == "2024"

    def test_same_window_returns_same_song_with_advancing_offset(
        self, client, db, monkeypatch
    ):
        _add_three_songs(db)

        # Pin time to the start of a UTC day: the shortest song is
        # 100s, so both probes land inside the day's first window.
        day_start = 20000 * 86400
        _freeze_time(monkeypatch, day_start + 10)
        first = client.get("/radio/now").get_json()
        _freeze_time(monkeypatch, day_start + 60)
        second = client.get("/radio/now").get_json()

        assert first["song"]["id"] == second["song"]["id"]
        assert first["offset"] == 10
        assert second["offset"] == 60
        assert first["slot_start"] == day_start
        # The window is exactly as long as the song itself.
        assert first["slot_end"] - first["slot_start"] == first["song"]["duration"]

    def test_windows_tile_the_day_gaplessly(self, client, db, monkeypatch):
        _add_three_songs(db)

        day_start = 20000 * 86400
        t = day_start + 1
        prev_end = day_start
        for _ in range(10):
            _freeze_time(monkeypatch, t)
            data = client.get("/radio/now").get_json()
            # Each window starts exactly where the previous one ended;
            # repeats of the same song are allowed.
            assert data["slot_start"] == prev_end
            assert data["slot_start"] <= t < data["slot_end"]
            assert data["slot_end"] - data["slot_start"] == data["song"]["duration"]
            prev_end = data["slot_end"]
            t = data["slot_end"] + 1

    def test_picks_vary_between_days(self, client, db, monkeypatch):
        _add_three_songs(db)

        def first_song_of_day(day):
            _freeze_time(monkeypatch, day * 86400 + 1)
            return client.get("/radio/now").get_json()["song"]["id"]

        # The pick sequence is seeded by the day number; almost any
        # range of days starts with more than one distinct song.
        firsts = {first_song_of_day(d) for d in range(20000, 20010)}
        assert len(firsts) > 1

    def test_song_crossing_midnight_is_cut_at_the_boundary(
        self, client, db, monkeypatch
    ):
        _add_three_songs(db)

        # 5 seconds before midnight: whatever is playing, its window
        # may not extend past the day boundary (shortest song is 100s,
        # so it would otherwise).
        day_end = 20001 * 86400
        _freeze_time(monkeypatch, day_end - 5)
        data = client.get("/radio/now").get_json()
        assert data["slot_end"] == day_end
        assert data["slot_end"] - data["slot_start"] < data["song"]["duration"]

    def test_synchronised_across_instances(self, client, db, monkeypatch, _seeded_db):
        _add_three_songs(db)

        other_app = create_app({"TESTING": True, "DATABASE_URI": _seeded_db})
        other_client = other_app.test_client()

        _freeze_time(monkeypatch, 20000 * 86400 + 142)
        first = client.get("/radio/now").get_json()
        second = other_client.get("/radio/now").get_json()
        assert first["song"] == second["song"]
        assert first["offset"] == second["offset"]


def test_radio_page(client):
    resp = client.get("/radio", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert b"Tune in" in resp.data
