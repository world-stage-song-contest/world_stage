"""Tests for the /api/year endpoints."""



def _result(resp):
    return resp.get_json()["result"]


def _error(resp):
    return resp.get_json()["error"]


def _seed_genres(db):
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO genre (id, name)
            VALUES (1, 'Pop')
            ON CONFLICT (id) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO subgenre (id, genre_id, name)
            VALUES (1, 1, 'Pop'), (2, 1, 'Synthpop')
            ON CONFLICT (id) DO NOTHING
        """)
    db.commit()


class TestYearIndex:
    def test_returns_all_years(self, client):
        resp = client.get("/api/year")
        assert resp.status_code == 200
        data = _result(resp)
        assert isinstance(data, list)
        assert len(data) >= 2  # 2024 (closed) and 2025 (open) from seed
        years = {y["year"] for y in data}
        assert 2024 in years
        assert 2025 in years

    def test_filter_by_closed(self, client):
        resp = client.get("/api/year", query_string={"type": "closed"})
        assert resp.status_code == 200
        data = _result(resp)
        assert all(y["status"] == "closed" for y in data)
        assert any(y["year"] == 2024 for y in data)

    def test_filter_by_open(self, client):
        resp = client.get("/api/year", query_string={"type": "open"})
        assert resp.status_code == 200
        data = _result(resp)
        assert all(y["status"] == "open" for y in data)
        assert any(y["year"] == 2025 for y in data)

    def test_year_json_shape(self, client):
        resp = client.get("/api/year")
        data = _result(resp)
        year = data[0]
        assert "year" in year
        assert "status" in year
        assert "host" in year


class TestYearById:
    def test_returns_year_with_counts(self, client, alice_headers):
        # Create a song so the year has entries
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "Test",
                "artist": "Artist",
                "sources": "http://example.com",
                "languages": [20],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/year/2025")
        assert resp.status_code == 200
        data = _result(resp)
        assert data["year"] == 2025
        assert data["status"] == "open"
        assert data["entry_count"] >= 1
        assert "host" in data

    def test_not_found(self, client):
        resp = client.get("/api/year/1800")
        assert resp.status_code == 404

    def test_host_included(self, client, alice_headers):
        # Ensure there's a song so year returns data
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "Test",
                "artist": "Artist",
                "sources": "http://example.com",
                "languages": [20],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/year/2025")
        data = _result(resp)
        host = data["host"]
        assert host is not None
        assert host["id"] == "US"
        assert host["name"] == "United States"


class TestYearSongs:
    def test_returns_songs_for_year(self, client, alice_headers):
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "Song A",
                "artist": "Artist A",
                "sources": "http://example.com",
                "languages": [20],
            },
            headers=alice_headers,
        )
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "ES",
                "title": "Song B",
                "artist": "Artist B",
                "sources": "http://example.com",
                "languages": [30],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/year/2025/songs")
        assert resp.status_code == 200
        data = _result(resp)
        assert isinstance(data, list)
        assert len(data) >= 2
        titles = {s["title"] for s in data}
        assert "Song A" in titles
        assert "Song B" in titles

    def test_empty_for_year_with_no_songs(self, client):
        resp = client.get("/api/year/2024/songs")
        assert resp.status_code == 200
        assert _result(resp) == []

    def test_song_json_shape(self, client, alice_headers):
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "FR",
                "title": "Chanson",
                "artist": "Chanteur",
                "sources": "http://example.com",
                "languages": [40],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/year/2025/songs")
        data = _result(resp)
        song = data[0]
        assert "id" in song
        assert "title" in song
        assert "artist" in song
        assert "country_id" in song
        assert "country_name" in song
        assert "year" in song
        assert "languages" in song
        assert "submitter_id" in song
        assert "submitter_name" in song
        assert "entry_number" in song
        assert "special_short_name" in song
        assert "title_language_id" in song
        assert "native_language_id" in song
        assert "duration" in song
        assert "vtt_link" in song
        assert "admin_approved" in song
        assert "key_signatures" in song
        assert "time_signatures" in song
        assert "subgenres" in song

    def test_song_list_includes_newer_song_fields(
        self, client, db, alice_headers, monkeypatch
    ):
        _seed_genres(db)
        monkeypatch.setattr("world_stage.media.probe_duration", lambda url: 123.5)
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "Expanded",
                "artist": "Artist",
                "sources": "http://example.com",
                "languages": [20],
                "video_link": "https://media.world-stage.org/test.mp4",
                "vtt_link": "https://example.com/test.vtt",
                "key_signatures": [
                    {"start_seconds": 0, "tonic": "C", "mode": "major"},
                ],
                "time_signatures": [
                    {"start_seconds": 0, "numerator": 4, "denominator": 4},
                ],
                "subgenres": [2],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/year/2025/songs")
        assert resp.status_code == 200
        song = next(row for row in _result(resp) if row["title"] == "Expanded")
        assert song["entry_number"] == 1
        assert song["special_short_name"] is None
        assert song["title_language_id"] == 20
        assert song["native_language_id"] == 20
        assert song["duration"] == 123.5
        assert song["vtt_link"] == "https://example.com/test.vtt"
        assert song["key_signatures"] == [
            {
                "start_seconds": 0,
                "tonic": "C",
                "mode": "major",
                "microtonal": False,
                "notes": None,
            }
        ]
        assert song["time_signatures"] == [
            {
                "start_seconds": 0,
                "numerator": 4,
                "denominator": 4,
                "notes": None,
            }
        ]
        assert song["subgenres"] == [
            {"id": 2, "name": "Synthpop", "genre_id": 1, "genre_name": "Pop"}
        ]

    def test_songs_ordered_by_country(self, client, alice_headers):
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "US Song",
                "artist": "A",
                "sources": "http://example.com",
                "languages": [20],
            },
            headers=alice_headers,
        )
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "FR",
                "title": "FR Song",
                "artist": "B",
                "sources": "http://example.com",
                "languages": [40],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/year/2025/songs")
        data = _result(resp)
        country_names = [s["country_name"] for s in data]
        assert country_names == sorted(country_names)
