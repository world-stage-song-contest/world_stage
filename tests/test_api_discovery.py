"""Tests for API discovery endpoints."""


def _result(resp):
    return resp.get_json()["result"]


def _seed_show(db):
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO show_status (name)
            VALUES ('none'), ('draw'), ('partial'), ('full')
            ON CONFLICT DO NOTHING
        """)
        cur.execute("""
            INSERT INTO point_system (id, number)
            VALUES (1, 1)
            ON CONFLICT (id) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO point (id, point_system_id, place, score)
            VALUES (1, 1, 1, 12), (2, 1, 2, 10), (3, 1, 3, 8)
            ON CONFLICT (id) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO show (
                id, year_id, point_system_id, show_name, short_name,
                voting_opens, voting_closes, predictions_close, date,
                dtf, sc, status
            )
            VALUES (
                1, 2025, 1, 'Final', 'f',
                CURRENT_TIMESTAMP - INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '30 minutes',
                DATE '2025-05-10', NULL, NULL, 'full'
            )
            ON CONFLICT DO NOTHING
        """)
    db.commit()


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


class TestMe:
    def test_anonymous(self, client):
        resp = client.get("/api/me")
        assert resp.status_code == 200
        data = _result(resp)
        assert data["authenticated"] is False
        assert data["user"] is None
        assert data["permissions"]["role"] == "none"

    def test_authenticated(self, client, alice_headers):
        resp = client.get("/api/me", headers=alice_headers)
        assert resp.status_code == 200
        data = _result(resp)
        assert data["authenticated"] is True
        assert data["user"] == {"id": 1, "username": "alice"}
        assert data["permissions"]["can_view_restricted"] is True


class TestReferenceDiscovery:
    def test_languages(self, client):
        resp = client.get("/api/language")
        assert resp.status_code == 200
        data = _result(resp)
        assert {row["tag"] for row in data} >= {"en", "es", "fr"}
        assert {"id", "name", "tag"} <= set(data[0])

    def test_genres(self, client, db):
        _seed_genres(db)
        resp = client.get("/api/genre")
        assert resp.status_code == 200
        data = _result(resp)
        pop = next(row for row in data if row["name"] == "Pop")
        assert [row["name"] for row in pop["subgenres"]] == ["Pop", "Synthpop"]

    def test_point_systems(self, client, db):
        _seed_show(db)
        resp = client.get("/api/point-system")
        assert resp.status_code == 200
        system = next(row for row in _result(resp) if row["id"] == 1)
        assert system["points"] == [12, 10, 8]


class TestShowDiscovery:
    def test_show_list(self, client, db):
        _seed_show(db)
        resp = client.get("/api/show", query_string={"year": 2025})
        assert resp.status_code == 200
        data = _result(resp)
        final = next(row for row in data if row["short_name"] == "f")
        assert final["key"] == "2025-f"
        assert final["display_name"] == "2025 Final"
        assert final["points"] == [12, 10, 8]

    def test_open_votings(self, client, db):
        _seed_show(db)
        resp = client.get("/api/voting/open")
        assert resp.status_code == 200
        data = _result(resp)
        final = next(row for row in data if row["key"] == "2025-f")
        assert final["predictions_open"] is True
        assert final["vote_count"] == 0


class TestSubmissionDiscovery:
    def test_submission_countries_anonymous(self, client):
        resp = client.get("/api/year/2025/submission-countries")
        assert resp.status_code == 200
        data = _result(resp)
        assert data["own"] == []
        assert {row["cc"] for row in data["placeholder"]} >= {"US", "ES", "FR"}

    def test_submission_countries_authenticated(self, client, bob_headers):
        resp = client.get("/api/year/2025/submission-countries", headers=bob_headers)
        assert resp.status_code == 200
        data = _result(resp)
        assert data["own"] == []
        assert {row["cc"] for row in data["placeholder"]} >= {"US", "ES", "FR"}

    def test_submission_context(self, client):
        resp = client.get("/api/submission-context")
        assert resp.status_code == 200
        data = _result(resp)
        assert 2025 in data["years"]["open"]
        assert 2024 in data["years"]["closed"]
        assert data["languages_url"] == "/api/language"
        assert data["genres_url"] == "/api/genre"
