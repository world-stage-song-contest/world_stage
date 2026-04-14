"""Tests for the /api/year endpoints."""

import pytest


def _result(resp):
    return resp.get_json()["result"]


def _error(resp):
    return resp.get_json()["error"]


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
        client.post("/api/song", json={
            "year": 2025, "country": "US",
            "title": "Test", "artist": "Artist",
            "sources": "http://example.com", "languages": [20],
        }, headers=alice_headers)

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
        client.post("/api/song", json={
            "year": 2025, "country": "US",
            "title": "Test", "artist": "Artist",
            "sources": "http://example.com", "languages": [20],
        }, headers=alice_headers)

        resp = client.get("/api/year/2025")
        data = _result(resp)
        host = data["host"]
        assert host is not None
        assert host["id"] == "US"
        assert host["name"] == "United States"


class TestYearSongs:
    def test_returns_songs_for_year(self, client, alice_headers):
        client.post("/api/song", json={
            "year": 2025, "country": "US",
            "title": "Song A", "artist": "Artist A",
            "sources": "http://example.com", "languages": [20],
        }, headers=alice_headers)
        client.post("/api/song", json={
            "year": 2025, "country": "ES",
            "title": "Song B", "artist": "Artist B",
            "sources": "http://example.com", "languages": [30],
        }, headers=alice_headers)

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
        client.post("/api/song", json={
            "year": 2025, "country": "FR",
            "title": "Chanson", "artist": "Chanteur",
            "sources": "http://example.com", "languages": [40],
        }, headers=alice_headers)

        resp = client.get("/api/year/2025/songs")
        data = _result(resp)
        song = data[0]
        assert "id" in song
        assert "title" in song
        assert "artist" in song
        assert "country" in song
        assert "year" in song
        assert "languages" in song
        assert "submitter" in song

    def test_songs_ordered_by_country(self, client, alice_headers):
        client.post("/api/song", json={
            "year": 2025, "country": "US",
            "title": "US Song", "artist": "A",
            "sources": "http://example.com", "languages": [20],
        }, headers=alice_headers)
        client.post("/api/song", json={
            "year": 2025, "country": "FR",
            "title": "FR Song", "artist": "B",
            "sources": "http://example.com", "languages": [40],
        }, headers=alice_headers)

        resp = client.get("/api/year/2025/songs")
        data = _result(resp)
        country_names = [s["country"]["name"] for s in data]
        assert country_names == sorted(country_names)
