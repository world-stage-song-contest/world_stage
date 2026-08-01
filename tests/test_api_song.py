"""Tests for the /api/song endpoints."""


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

    def test_reuses_identical_ordered_language_sets(
        self, client, db, alice_headers
    ):
        first = _result(
            _create_song(client, alice_headers, country="US", languages=[20, 30])
        )
        second = _result(
            _create_song(client, alice_headers, country="ES", languages=[20, 30])
        )

        with db.cursor() as cursor:
            cursor.execute(
                """SELECT language_set_id FROM current_song
                   WHERE id = ANY(%s) ORDER BY id""",
                ([first["id"], second["id"]],),
            )
            set_ids = [row["language_set_id"] for row in cursor]
            cursor.execute(
                """SELECT COUNT(*) AS count FROM language_set
                   WHERE language_ids = %s::bigint[]""",
                ([20, 30],),
            )
            assert cursor.fetchone()["count"] == 1
        assert set_ids[0] == set_ids[1]

    def test_language_order_defines_distinct_sets(self, client, db, alice_headers):
        _create_song(client, alice_headers, country="US", languages=[20, 30])
        _create_song(client, alice_headers, country="ES", languages=[30, 20])

        with db.cursor() as cursor:
            cursor.execute(
                """SELECT language_ids FROM language_set
                   WHERE language_ids IN (%s::bigint[], %s::bigint[])""",
                ([20, 30], [30, 20]),
            )
            assert {tuple(row["language_ids"]) for row in cursor} == {
                (20, 30), (30, 20)
            }

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
    def test_creates_song(self, client, db, bob_headers):
        resp = _create_song(client, bob_headers)
        assert resp.status_code == 201
        data = _result(resp)
        assert data["title"] == "Test Song"
        assert data["submitter_id"] == 2  # bob
        assert "approval_status" not in data
        assert "admin_approved" not in data
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT changed_by FROM current_song WHERE id = %s",
                (data["id"],),
            )
            assert cursor.fetchone()["changed_by"] == 2

    def test_create_cannot_set_approval_status(self, client, db, alice_headers):
        resp = _create_song(client, alice_headers, approval_status="accepted")
        assert resp.status_code == 201
        data = _result(resp)
        assert "approval_status" not in data
        with db.cursor() as cursor:
            cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (data["id"],))
            assert cursor.fetchone()["approval_status"] == "pending"

    def test_returns_location_header(self, client, bob_headers):
        resp = _create_song(client, bob_headers)
        assert resp.status_code == 201
        assert "Location" in resp.headers
        song_id = _result(resp)["id"]
        assert f"/api/song/{song_id}" in resp.headers["Location"]

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": "US",
                "title": "X",
                "artist": "Y",
                "sources": "Z",
                "languages": [20],
            },
        )
        assert resp.status_code == 401

    def test_assigns_next_entry_number_for_same_country(self, client, bob_headers):
        first = _create_song(client, bob_headers, country="US")
        resp = _create_song(client, bob_headers, country="US")
        assert first.status_code == 201
        assert resp.status_code == 201
        assert _result(first)["entry_number"] == 1
        assert _result(resp)["entry_number"] == 2

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

    def test_admin_cannot_skip_artist_and_title(self, client, alice_headers):
        resp = _create_song(client, alice_headers, title="", artist="", sources="")
        assert resp.status_code == 400

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
        resp = client.post(
            "/api/song",
            data={
                "year": "2025",
                "country": "US",
                "title": "Form Song",
                "artist": "Form Artist",
                "sources": "http://example.com",
                "language": ["20", "30"],
            },
            headers={
                "Authorization": "Bearer token-bob",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
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
        resp = _create_song(client, bob_headers, snippet_start="0:00", snippet_end="0:30")
        assert resp.status_code == 400

    def test_valid_snippet(self, client, bob_headers):
        resp = _create_song(client, bob_headers, snippet_start="1:00", snippet_end="1:15")
        assert resp.status_code == 201

    def test_second_snippet_is_optional(self, client, bob_headers):
        resp = _create_song(client, bob_headers, snippet2_start="1:00")

        assert resp.status_code == 201
        assert _result(resp)["snippet2_start"] == "1:00"
        assert _result(resp)["snippet2_end"] is None

    def test_second_snippet_duration_limit(self, client, bob_headers):
        resp = _create_song(
            client,
            bob_headers,
            snippet2_start="0:00",
            snippet2_end="0:11",
        )

        assert resp.status_code == 400

    def test_valid_second_snippet(self, client, bob_headers):
        resp = _create_song(
            client,
            bob_headers,
            snippet2_start="1:00",
            snippet2_end="1:10",
        )

        assert resp.status_code == 201
        assert _result(resp)["snippet2_start"] == "1:00"
        assert _result(resp)["snippet2_end"] == "1:10"


# ── PATCH /api/song/<id> ────────────────────────────────────────────


class TestUpdateSong:
    def test_updates_title(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(
            f"/api/song/{song_id}", json={"title": "New Title"}, headers=bob_headers
        )
        assert resp.status_code == 200
        assert _result(resp)["title"] == "New Title"

    def test_updates_multiple_fields(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(
            f"/api/song/{song_id}",
            json={
                "title": "Updated",
                "artist": "New Artist",
                "notes": "Some notes",
            },
            headers=bob_headers,
        )
        assert resp.status_code == 200
        data = _result(resp)
        assert data["title"] == "Updated"
        assert data["artist"] == "New Artist"
        assert data["notes"] == "Some notes"

    def test_placeholder_change_only_appends_song_status(
        self, client, db, bob_headers
    ):
        song_id = _result(_create_song(client, bob_headers))["id"]

        response = client.patch(
            f"/api/song/{song_id}",
            json={"is_placeholder": True},
            headers=bob_headers,
        )

        assert response.status_code == 200
        assert _result(response)["is_placeholder"] is True
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s",
                (song_id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT COUNT(*) AS count FROM song_status WHERE song_id = %s",
                (song_id,),
            )
            assert cursor.fetchone()["count"] == 2

    def test_logs_second_snippet_changes(self, client, db, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(
            f"/api/song/{song_id}",
            json={"snippet2_start": "1:00", "snippet2_end": "1:10"},
            headers=bob_headers,
        )

        assert resp.status_code == 200
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT changed_by, changed_fields
                FROM song_change
                WHERE song_id = %s AND event_type = 'song_modification'
                ORDER BY id DESC
                LIMIT 1
                """,
                (song_id,),
            )
            audit = cur.fetchone()
        assert audit["changed_by"] == 2
        assert audit["changed_fields"]["snippet2_start"] == {"old": None, "new": 60}
        assert audit["changed_fields"]["snippet2_end"] == {"old": None, "new": 70}

    def test_updates_languages(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers, languages=[20]))["id"]

        resp = client.patch(
            f"/api/song/{song_id}", json={"languages": [30, 40]}, headers=bob_headers
        )
        assert resp.status_code == 200
        langs = _result(resp)["languages"]
        assert [lang["id"] for lang in langs] == [30, 40]

    def test_language_change_is_stored_on_the_new_revision(
        self, client, db, bob_headers
    ):
        song_id = _result(
            _create_song(client, bob_headers, languages=[20])
        )["id"]

        response = client.patch(
            f"/api/song/{song_id}", json={"languages": [30, 40]},
            headers=bob_headers,
        )
        assert response.status_code == 200

        with db.cursor() as cursor:
            cursor.execute(
                """SELECT language_set.language_ids
                   FROM song_data
                   JOIN language_set
                     ON language_set.id = song_data.language_set_id
                   WHERE song_data.song_id = %s
                   ORDER BY song_data.created_at, song_data.id""",
                (song_id,),
            )
            assert [row["language_ids"] for row in cursor] == [[20], [30, 40]]

    def test_requires_auth(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={"title": "X"})
        assert resp.status_code == 401

    def test_owner_can_edit_own(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={"title": "Updated"}, headers=bob_headers)
        assert resp.status_code == 200

    def test_non_owner_cannot_edit(self, client, bob_headers, carol_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={"title": "Hijack"}, headers=carol_headers)
        assert resp.status_code == 403

    def test_admin_can_edit_any(self, client, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(
            f"/api/song/{song_id}", json={"title": "Admin Edit"}, headers=alice_headers
        )
        assert resp.status_code == 200
        assert _result(resp)["title"] == "Admin Edit"

    def test_not_found(self, client, alice_headers):
        resp = client.patch("/api/song/999999", json={"title": "X"}, headers=alice_headers)
        assert resp.status_code == 404

    def test_empty_body_rejected(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={}, headers=bob_headers)
        assert resp.status_code == 400

    def test_patch_cannot_set_approval_status(
        self, client, db, bob_headers, alice_headers
    ):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(
            f"/api/song/{song_id}",
            json={"approval_status": "accepted", "title": "T"},
            headers=alice_headers,
        )
        assert resp.status_code == 200
        assert "approval_status" not in _result(resp)
        with db.cursor() as cursor:
            cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
            assert cursor.fetchone()["approval_status"] == "pending"

    def test_clears_nullable_field(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers, video_link="http://example.com"))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={"video_link": ""}, headers=bob_headers)
        assert resp.status_code == 200
        assert _result(resp)["video_link"] is None


class TestSongDuration:
    """video_link writes keep the probed duration column in sync."""

    MEDIA_LINK = "https://media.world-stage.org/ws2025us.mp4"

    def _stored_duration(self, db, song_id):
        with db.cursor() as cur:
            cur.execute("SELECT duration FROM current_song WHERE id = %s", (song_id,))
            return cur.fetchone()["duration"]

    def test_create_with_media_link_probes_duration(self, client, db, bob_headers, monkeypatch):
        monkeypatch.setattr("world_stage.media.probe_duration", lambda url: 187.5)
        song_id = _result(_create_song(client, bob_headers, video_link=self.MEDIA_LINK))["id"]
        assert self._stored_duration(db, song_id) == 187.5

    def test_create_with_external_link_does_not_probe(self, client, db, bob_headers, monkeypatch):
        def boom(url):
            raise AssertionError("probe_duration must not be called")

        monkeypatch.setattr("world_stage.media.probe_duration", boom)
        song_id = _result(_create_song(client, bob_headers, video_link="http://example.com/x"))[
            "id"
        ]
        assert self._stored_duration(db, song_id) is None

    def test_patch_to_media_link_sets_duration(self, client, db, bob_headers, monkeypatch):
        song_id = _result(_create_song(client, bob_headers, video_link="http://example.com/x"))[
            "id"
        ]

        monkeypatch.setattr("world_stage.media.probe_duration", lambda url: 203.0)
        resp = client.patch(
            f"/api/song/{song_id}", json={"video_link": self.MEDIA_LINK}, headers=bob_headers
        )
        assert resp.status_code == 200
        assert self._stored_duration(db, song_id) == 203.0

    def test_patch_to_external_link_clears_duration(self, client, db, bob_headers, monkeypatch):
        monkeypatch.setattr("world_stage.media.probe_duration", lambda url: 203.0)
        song_id = _result(_create_song(client, bob_headers, video_link=self.MEDIA_LINK))["id"]

        resp = client.patch(
            f"/api/song/{song_id}", json={"video_link": "http://example.com/x"}, headers=bob_headers
        )
        assert resp.status_code == 200
        assert self._stored_duration(db, song_id) is None

    def test_patch_with_unchanged_link_keeps_duration_without_reprobe(
        self, client, db, bob_headers, monkeypatch
    ):
        monkeypatch.setattr("world_stage.media.probe_duration", lambda url: 203.0)
        song_id = _result(_create_song(client, bob_headers, video_link=self.MEDIA_LINK))["id"]

        def boom(url):
            raise AssertionError("unchanged link must not re-probe")

        monkeypatch.setattr("world_stage.media.probe_duration", boom)
        resp = client.patch(
            f"/api/song/{song_id}",
            json={"video_link": self.MEDIA_LINK, "notes": "edited"},
            headers=bob_headers,
        )
        assert resp.status_code == 200
        assert self._stored_duration(db, song_id) == 203.0

    def test_backfill_command(self, app, db, monkeypatch):
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO song (country_id, year_id)
                VALUES ('US', 2024)
                RETURNING id
                """,
            )
            song_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist, video_link
                   ) VALUES (%s, 1, 'Backfill me', 'Artist', %s)""",
                (song_id, self.MEDIA_LINK),
            )
        db.commit()

        monkeypatch.setattr("world_stage.media.probe_duration", lambda url: 154.2)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["backfill-durations"])
        assert "1 updated, 0 failed" in result.output
        assert self._stored_duration(db, song_id) == 154.2

    def test_admin_update_duration_endpoint_requires_auth(self, client, db, bob_headers):
        song_id = _result(_create_song(client, bob_headers, video_link=self.MEDIA_LINK))["id"]
        resp = client.post(f"/country/duration/{song_id}")
        assert resp.status_code == 403

    def test_non_admin_cannot_clear_required(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.patch(f"/api/song/{song_id}", json={"title": ""}, headers=bob_headers)
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
    def test_replaces_all_fields(self, client, db, bob_headers):
        song_id = _result(
            _create_song(client, bob_headers, notes="old notes", video_link="http://old.com")
        )["id"]

        resp = _put_song(
            client,
            bob_headers,
            song_id,
            title="New Title",
            artist="New Artist",
            sources="http://new.com",
            languages=[30],
        )
        assert resp.status_code == 200
        data = _result(resp)
        assert data["title"] == "New Title"
        assert data["artist"] == "New Artist"
        assert data["sources"] == "http://new.com"
        assert data["languages"][0]["id"] == 30
        # Fields not included in PUT body are cleared
        assert data["notes"] is None
        assert data["video_link"] is None
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT changed_by FROM current_song WHERE id = %s",
                (song_id,),
            )
            assert cursor.fetchone()["changed_by"] == 2

    def test_requires_auth(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.put(
            f"/api/song/{song_id}",
            json={
                "title": "X",
                "artist": "Y",
                "sources": "Z",
                "languages": [20],
            },
        )
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

    def test_admin_cannot_clear_artist_and_title(self, client, alice_headers):
        song_id = _result(_create_song(client, alice_headers))["id"]

        resp = _put_song(client, alice_headers, song_id, title="", artist="", sources="")
        assert resp.status_code == 400

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

    def test_put_cannot_set_approval_status(self, client, db, bob_headers, alice_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, alice_headers, song_id, approval_status="accepted")
        assert resp.status_code == 200
        assert "approval_status" not in _result(resp)
        with db.cursor() as cursor:
            cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
            assert cursor.fetchone()["approval_status"] == "pending"

    def test_snippet_duration_limit(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(client, bob_headers, song_id, snippet_start="0:00", snippet_end="0:30")
        assert resp.status_code == 400

    def test_second_snippet_duration_limit(self, client, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = _put_song(
            client,
            bob_headers,
            song_id,
            snippet2_start="0:00",
            snippet2_end="0:11",
        )

        assert resp.status_code == 400


# ── DELETE /api/song/<id> ───────────────────────────────────────────


class TestDeleteSong:
    def test_deletes_song(self, client, db, bob_headers):
        song_id = _result(_create_song(client, bob_headers))["id"]

        resp = client.delete(f"/api/song/{song_id}", headers=bob_headers)
        assert resp.status_code == 204
        assert resp.data == b""

        # Verify it's gone
        resp = client.get(f"/api/song/{song_id}")
        assert resp.status_code == 404
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT changed_by
                FROM song_data
                WHERE country_id = 'US' AND year_id = 2025
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
            assert cursor.fetchone()["changed_by"] == 2

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

    # The create API rejects non-open years for everyone, so songs in
    # a closed year are seeded directly in the database.
    @staticmethod
    def _insert_closed_year_song(db, submitter_id=2):
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO song (country_id, year_id)
                VALUES ('US', 2024)
                RETURNING id
                """,
            )
            song_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist
                   ) VALUES (%s, %s, 'Closed Song', 'Artist')""",
                (song_id, submitter_id),
            )
        db.commit()
        return song_id

    def test_cannot_delete_closed_year(self, client, db, bob_headers):
        song_id = self._insert_closed_year_song(db)

        resp = client.delete(f"/api/song/{song_id}", headers=bob_headers)
        assert resp.status_code == 403

    def test_admin_can_delete_closed_year(self, client, db, bob_headers, alice_headers):
        song_id = self._insert_closed_year_song(db)

        resp = client.delete(f"/api/song/{song_id}", headers=alice_headers)
        assert resp.status_code == 204
