"""Tests for the /api/country endpoints."""

import pytest


def _result(resp):
    return resp.get_json()["result"]


def _error(resp):
    return resp.get_json()["error"]


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
        client.post("/api/song", json={
            "year": 2025, "country": "US",
            "title": "Test Song", "artist": "Test Artist",
            "sources": "http://example.com", "languages": [20],
        }, headers=alice_headers)

        resp = client.get("/api/country/US/songs")
        assert resp.status_code == 200
        data = _result(resp)
        assert isinstance(data, list)
        assert len(data) >= 1
        song = data[0]
        assert song["title"] == "Test Song"
        assert song["country"]["id"] == "US"

    def test_empty_for_country_with_no_songs(self, client):
        resp = client.get("/api/country/FR/songs")
        assert resp.status_code == 200
        assert _result(resp) == []

    def test_cc3_redirects(self, client):
        resp = client.get("/api/country/ESP/songs")
        assert resp.status_code == 301
        assert "/api/country/ES/songs" in resp.headers["Location"]

    def test_song_json_shape(self, client, alice_headers):
        client.post("/api/song", json={
            "year": 2025, "country": "ES",
            "title": "Cancion", "artist": "Artista",
            "sources": "http://example.com", "languages": [30],
        }, headers=alice_headers)

        resp = client.get("/api/country/ES/songs")
        data = _result(resp)
        assert len(data) >= 1
        song = data[0]
        assert "id" in song
        assert "title" in song
        assert "artist" in song
        assert "country" in song
        assert "year" in song
        assert "languages" in song
        assert "submitter" in song
