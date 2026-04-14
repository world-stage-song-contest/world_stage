"""Tests for the /api/song endpoints."""

import json

import pytest


# ── helpers ─────────────────────────────────────────────────────────

def _create_song(client, headers, **overrides):
    """POST a minimal valid song and return the response."""
    body = {
        "year": 2025,
        "country": "US",
        "title": "Test Song",
        "artist": "Test Artist",
        "sources": "http://example.com",
        "languages": [20],
        **overrides,
    }
    return client.post("/api/song", json=body, headers=headers)


def _result(resp):
    """Extract the 'result' key from a successful JSON response."""
    return resp.get_json()["result"]


def _error(resp):
    """Extract the 'error' key from an error JSON response."""
    return resp.get_json()["error"]


# ── GET /api/song/<id> ──────────────────────────────────────────────

class TestGetSongById:
    def test_returns_song(self, client, alice_headers):
        create = _create_song(client, alice_headers)
        song_id = _result(create)["id"]

        resp = client.get(f"/api/song/{song_id}")
        assert resp.status_code == 200
        data = _result(resp)
        assert data["id"] == song_id
        assert data["title"] == "Test Song"
        assert data["artist"] == "Test Artist"
        assert data["country_id"] == "US"
        assert data["year"] == 2025

    def test_includes_languages(self, client, alice_headers):
        create = _create_song(client, alice_headers, languages=[20, 30])
        song_id = _result(create)["id"]

        resp = client.get(f"/api/song/{song_id}")
        langs = _result(resp)["languages"]
        assert len(langs) == 2
        assert langs[0]["id"] == 20
        assert langs[1]["id"] == 30

    def test_not_found(self, client):
        resp = client.get("/api/song/999999")
        assert resp.status_code == 404
        assert _error(resp)["id"] == 1  # ErrorID.NOT_FOUND


# ── GET /api/song/<cc>/<year> ───────────────────────────────────────

class TestGetSongByCountryYear:
    def test_returns_song_by_cc2(self, client, alice_headers):
        _create_song(client, alice_headers, country="US", year=2025)

        resp = client.get("/api/song/us/2025")
        assert resp.status_code == 200
        data = _result(resp)
        assert data["country_id"] == "US"
        assert data["year"] == 2025

    def test_cc3_redirects_to_cc2(self, client, alice_headers):
        _create_song(client, alice_headers, country="ES", year=2025)

        resp = client.get("/api/song/esp/2025")
        assert resp.status_code == 301
        assert "/api/song/es/2025" in resp.headers["Location"]

    def test_not_found_country(self, client):
        resp = client.get("/api/song/zz/2025")
        assert resp.status_code == 404

    def test_not_found_year(self, client, alice_headers):
        _create_song(client, alice_headers, country="US", year=2025)

        resp = client.get("/api/song/us/1800")
        assert resp.status_code == 404


# ── POST /api/song ──────────────────────────────────────────────────

class TestCreateSong:
    def test_creates_song(self, client, bob_headers):
        resp = _create_song(client, bob_headers)
        assert resp.status_code == 201
        data = _result(resp)
        assert data["title"] == "Test Song"
        assert data["submitter_id"] == 2  # bob

    def test_returns_location_header(self, client, bob_headers):
        resp = _create_song(client, bob_headers)
        assert resp.status_code == 201
        assert "Location" in resp.headers
        song_id = _result(resp)["id"]
        assert f"/api/song/{song_id}" in resp.headers["Location"]

    def test_requires_auth(self, client):
        resp = client.post("/api/song", json={
            "year": 2025, "country": "US", "title": "X",
            "artist": "Y", "sources": "Z", "languages": [20],
        })
        assert resp.status_code == 401

    def test_rejects_duplicate(self, client, bob_headers):
        _create_song(client, bob_headers, country="US")
        resp = _create_song(client, bob_headers, country="US")
        assert resp.status_code == 409

    def test_requires_year_and_country(self, client, bob_headers):
        resp = client.post("/api/song", json={"title": "X"}, headers=bob_headers)
        assert resp.status_code == 400

    def test_requires_languages(self, client, bob_headers):
        resp = _create_song(client, bob_headers, languages=[])
        assert resp.status_code == 400

    def test_requires_title_for_non_admin(self, client, bob_headers):
        resp = _create_song(client, bob_headers, title="")
        assert resp.status_code == 400

    def test_requires_artist_for_non_admin(self, client, bob_headers):
        resp = _create_song(client, bob_headers, artist="")
        assert resp.status_code == 400

    def test_requires_sources_for_non_admin(self, client, bob_headers):
        resp = _create_song(client, bob_headers, sources="")
        assert resp.status_code == 400

    def test_admin_can_skip_required_fields(self, client, alice_headers):
        resp = _create_song(client, alice_headers, title="", artist="", sources="")
        assert resp.status_code == 201

    def test_admin_can_set_submitter_id(self, client, alice_headers):
        resp = _create_song(client, alice_headers, submitter_id=2)
        assert resp.status_code == 201
        assert _result(resp)["submitter_id"] == 2

    def test_non_admin_cannot_set_submitter_id(self, client, bob_headers):
        resp = _create_song(client, bob_headers, submitter_id=1)
        assert resp.status_code == 201
        assert _result(resp)["submitter_id"] == 2  # still bob, not alice

    def test_submission_limit_per_user(self, client, bob_headers, alice_headers):
        _create_song(client, bob_headers, country="US")
        _create_song(client, bob_headers, country="ES")
        resp = _create_song(client, bob_headers, country="FR")
        assert resp.status_code == 403

    def test_admin_bypasses_submission_limit(self, client, alice_headers):
        _create_song(client, alice_headers, country="US")
        _create_song(client, alice_headers, country="ES")
        resp = _create_song(client, alice_headers, country="FR")
        assert resp.status_code == 201

    def test_form_encoded(self, client, bob_headers):
        resp = client.post("/api/song", data={
            "year": "2025",
            "country": "US",
            "title": "Form Song",
            "artist": "Form Artist",
            "sources": "http://example.com",
            "language": ["20", "30"],
        }, headers={
            "Authorization": "Bearer token-bob",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        assert resp.status_code == 201
        data = _result(resp)
        assert data["title"] == "Form Song"
        assert len(data["languages"]) == 2

    def test_unknown_year(self, client, bob_headers):
        resp = _create_song(client, bob_headers, year=1800)
        assert resp.status_code == 404

    def test_unknown_country(self, client, bob_headers):
        resp = _create_song(client, bob_headers, country="ZZ")
        assert resp.status_code == 404

    def test_snippet_duration_limit(self, client, bob_headers):
        resp = _create_song(client, bob_headers,
                            snippet_start="0:00", snippet_end="0:30")
        assert resp.status_code == 400

    def test_valid_snippet(self, client, bob_headers):
        resp = _create_song(client, bob_headers,
                            snippet_start="1:00", snippet_end="1:15")
        assert resp.status_code == 201


# ── PATCH /api/song/<id> ────────────────────────────────────────────

class TestUpdateSong:
    def test_updates_title(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"title": "New Title"}, headers=bob_headers)
        assert resp.status_code == 200
        assert _result(resp)["title"] == "New Title"

    def test_updates_multiple_fields(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={
            "title": "Updated",
            "artist": "New Artist",
            "notes": "Some notes",
        }, headers=bob_headers)
        assert resp.status_code == 200
        data = _result(resp)
        assert data["title"] == "Updated"
        assert data["artist"] == "New Artist"
        assert data["notes"] == "Some notes"

    def test_updates_languages(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers, languages=[20]))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"languages": [30, 40]}, headers=bob_headers)
        assert resp.status_code == 200
        langs = _result(resp)["languages"]
        assert [l["id"] for l in langs] == [30, 40]

    def test_requires_auth(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={"title": "X"})
        assert resp.status_code == 401

    def test_owner_can_edit_own(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"title": "Updated"}, headers=bob_headers)
        assert resp.status_code == 200

    def test_non_owner_cannot_edit(self, client, bob_headers, carol_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"title": "Hijack"}, headers=carol_headers)
        assert resp.status_code == 403

    def test_admin_can_edit_any(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"title": "Admin Edit"}, headers=alice_headers)
        assert resp.status_code == 200
        assert _result(resp)["title"] == "Admin Edit"

    def test_not_found(self, client, alice_headers):
        resp = client.patch("/api/song/999999",
                            json={"title": "X"}, headers=alice_headers)
        assert resp.status_code == 404

    def test_empty_body_rejected(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={}, headers=bob_headers)
        assert resp.status_code == 400

    def test_admin_approved_only_by_admin(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        # bob can't set admin_approved
        resp = client.patch(f"/api/song/{song_id}",
                            json={"admin_approved": True, "title": "T"},
                            headers=bob_headers)
        assert resp.status_code == 200
        assert _result(resp)["admin_approved"] is False

        # alice can
        resp = client.patch(f"/api/song/{song_id}",
                            json={"admin_approved": True},
                            headers=alice_headers)
        assert resp.status_code == 200
        assert _result(resp)["admin_approved"] is True

    def test_clears_nullable_field(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers,
                                       video_link="http://example.com"))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"video_link": ""}, headers=bob_headers)
        assert resp.status_code == 200
        assert _result(resp)["video_link"] is None

    def test_non_admin_cannot_clear_required(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}",
                            json={"title": ""}, headers=bob_headers)
        assert resp.status_code == 400


# ── PUT /api/song/<id> ──────────────────────────────────────────────

def _put_song(client, headers, song_id, **overrides):
    """PUT a full song replacement."""
    body = {
        "year": 2025,
        "country": "US",
        "title": "Replaced Title",
        "artist": "Replaced Artist",
        "sources": "http://replaced.com",
        "languages": [20],
        **overrides,
    }
    return client.put(f"/api/song/{song_id}", json=body, headers=headers)


class TestReplaceSong:
    def test_replaces_all_fields(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers,
                                       notes="old notes", video_link="http://old.com"))["id"]

        resp = _put_song(client, bob_headers, song_id,
                         title="New Title", artist="New Artist",
                         sources="http://new.com", languages=[30])
        assert resp.status_code == 200
        data = _result(resp)
        assert data["title"] == "New Title"
        assert data["artist"] == "New Artist"
        assert data["sources"] == "http://new.com"
        assert data["languages"][0]["id"] == 30
        # Fields not included in PUT body are cleared
        assert data["notes"] is None
        assert data["video_link"] is None

    def test_requires_auth(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.put(f"/api/song/{song_id}", json={
            "title": "X", "artist": "Y", "sources": "Z", "languages": [20],
        })
        assert resp.status_code == 401

    def test_not_found(self, client, alice_headers):
        resp = _put_song(client, alice_headers, 999999)
        assert resp.status_code == 404

    def test_owner_can_replace(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, bob_headers, song_id)
        assert resp.status_code == 200

    def test_non_owner_cannot_replace(self, client, bob_headers, carol_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, carol_headers, song_id)
        assert resp.status_code == 403

    def test_admin_can_replace_any(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, alice_headers, song_id, title="Admin Replace")
        assert resp.status_code == 200
        assert _result(resp)["title"] == "Admin Replace"

    def test_requires_languages(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, bob_headers, song_id, languages=[])
        assert resp.status_code == 400

    def test_requires_title_for_non_admin(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, bob_headers, song_id, title="")
        assert resp.status_code == 400

    def test_admin_can_clear_required(self, client, alice_headers):
        song_id = _result(_create_song(client, alice_headers))["id"]

        resp = _put_song(client, alice_headers, song_id,
                         title="", artist="", sources="")
        assert resp.status_code == 200

    def test_replaces_languages(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers, languages=[20, 30]))["id"]

        resp = _put_song(client, bob_headers, song_id, languages=[40])
        assert resp.status_code == 200
        langs = _result(resp)["languages"]
        assert len(langs) == 1
        assert langs[0]["id"] == 40

    def test_preserves_submitter(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, bob_headers, song_id)
        assert resp.status_code == 200
        assert _result(resp)["submitter_id"] == 2  # still bob

    def test_admin_can_change_submitter(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, alice_headers, song_id, submitter_id=3)
        assert resp.status_code == 200
        assert _result(resp)["submitter_id"] == 3

    def test_admin_approved_only_by_admin(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        # bob can't set admin_approved
        resp = _put_song(client, bob_headers, song_id, admin_approved=True)
        assert resp.status_code == 200
        assert _result(resp)["admin_approved"] is False

        # alice can
        resp = _put_song(client, alice_headers, song_id, admin_approved=True)
        assert resp.status_code == 200
        assert _result(resp)["admin_approved"] is True

    def test_snippet_duration_limit(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, bob_headers, song_id,
                         snippet_start="0:00", snippet_end="0:30")
        assert resp.status_code == 400


# ── DELETE /api/song/<id> ───────────────────────────────────────────

class TestDeleteSong:
    def test_deletes_song(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=bob_headers)
        assert resp.status_code == 204
        assert resp.data == b""

        # Verify it's gone
        resp = client.get(f"/api/song/{song_id}")
        assert resp.status_code == 404

    def test_returns_204_for_nonexistent(self, client, alice_headers):
        resp = client.delete("/api/song/999999", headers=alice_headers)
        assert resp.status_code == 204

    def test_requires_auth(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.delete(f"/api/song/{song_id}")
        assert resp.status_code == 401

    def test_owner_can_delete_own(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=bob_headers)
        assert resp.status_code == 204

    def test_non_owner_cannot_delete(self, client, bob_headers, carol_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=carol_headers)
        assert resp.status_code == 403

    def test_admin_can_delete_any(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=alice_headers)
        assert resp.status_code == 204

    def test_cannot_delete_closed_year(self, client, bob_headers):
        create = _create_song(client, bob_headers, year=2024)
        song_id = _result(create)["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=bob_headers)
        assert resp.status_code == 403

    def test_admin_can_delete_closed_year(self, client, bob_headers, alice_headers):
        create = _create_song(client, bob_headers, year=2024)
        song_id = _result(create)["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=alice_headers)
        assert resp.status_code == 204
