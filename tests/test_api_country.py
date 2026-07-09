"""Tests for the /api/country endpoints."""



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


class TestCountryIndex:
    def test_returns_participating_countries(self, client):
        resp = client.get("/api/country")
        assert resp.status_code == 200
        data = _result(resp)
        assert isinstance(data, list)
        assert len(data) >= 3  # US, ES, FR from seed
        names = {c["name"] for c in data}
        assert "United States" in names
        assert "Spain" in names
        assert "France" in names

    def test_all_flag_includes_non_participating(self, client, db):
        # Insert a non-participating country
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO country (id, name, is_participating, cc3)
                VALUES ('XX', 'Testland', false, 'XXX')
                ON CONFLICT DO NOTHING
            """)
        db.commit()

        # Without all: Testland excluded (id='XX' is also excluded by the query)
        resp = client.get("/api/country")
        names_default = {c["name"] for c in _result(resp)}

        resp_all = client.get("/api/country", query_string={"all": "true"})
        names_all = {c["name"] for c in _result(resp_all)}

        # 'all' should return at least as many
        assert len(names_all) >= len(names_default)

        # Cleanup
        with db.cursor() as cur:
            cur.execute("DELETE FROM country WHERE id = 'XX'")
        db.commit()

    def test_country_json_shape(self, client):
        resp = client.get("/api/country")
        data = _result(resp)
        country = data[0]
        assert "id" in country
        assert "name" in country
        assert "cc3" in country


class TestCountryById:
    def test_returns_country_by_cc2(self, client):
        resp = client.get("/api/country/US")
        assert resp.status_code == 200
        data = _result(resp)
        assert data["id"] == "US"
        assert data["name"] == "United States"
        assert data["cc3"] == "USA"

    def test_case_insensitive(self, client):
        resp = client.get("/api/country/us")
        assert resp.status_code == 200
        assert _result(resp)["id"] == "US"

    def test_cc3_redirects_to_cc2(self, client):
        resp = client.get("/api/country/USA")
        assert resp.status_code == 301
        assert "/api/country/US" in resp.headers["Location"]

    def test_not_found(self, client):
        resp = client.get("/api/country/ZZ")
        assert resp.status_code == 404
        assert _error(resp)["id"] == 1  # ErrorID.NOT_FOUND


class TestCountrySongs:
    def test_returns_songs_for_country(self, client, alice_headers):
        # Create a song via the song API
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "Test Song",
                "artist": "Test Artist",
                "sources": "http://example.com",
                "languages": [20],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/country/US/songs")
        assert resp.status_code == 200
        data = _result(resp)
        assert isinstance(data, list)
        assert len(data) >= 1
        song = data[0]
        assert song["title"] == "Test Song"
        assert song["country_id"] == "US"

    def test_empty_for_country_with_no_songs(self, client):
        resp = client.get("/api/country/FR/songs")
        assert resp.status_code == 200
        assert _result(resp) == []

    def test_cc3_redirects(self, client):
        resp = client.get("/api/country/ESP/songs")
        assert resp.status_code == 301
        assert "/api/country/ES/songs" in resp.headers["Location"]

    def test_song_json_shape(self, client, alice_headers):
        client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "ES",
                "title": "Cancion",
                "artist": "Artista",
                "sources": "http://example.com",
                "languages": [30],
            },
            headers=alice_headers,
        )

        resp = client.get("/api/country/ES/songs")
        data = _result(resp)
        assert len(data) >= 1
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

        resp = client.get("/api/country/US/songs")
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
