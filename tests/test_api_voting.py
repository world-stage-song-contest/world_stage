"""Tests for authenticated ballot and prediction API endpoints."""

from world_stage.utils import get_show_result_entries, get_year_index_winners


def _result(response):
    return response.get_json()["result"]


def _seed_show_and_songs(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (10, 1) ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """
            INSERT INTO point (id, point_system_id, place, score)
            VALUES (101, 10, 1, 12), (102, 10, 2, 10), (103, 10, 3, 8)
            ON CONFLICT (id) DO UPDATE
            SET point_system_id = EXCLUDED.point_system_id,
                place = EXCLUDED.place,
                score = EXCLUDED.score
            """
        )
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_type,
                voting_opens, voting_closes, predictions_close, status
            )
            VALUES (
                2025, 10, 'f',
                CURRENT_TIMESTAMP - INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '30 minutes', 'full'
            )
            ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL DO UPDATE
            SET point_system_id = EXCLUDED.point_system_id,
                voting_opens = EXCLUDED.voting_opens,
                voting_closes = EXCLUDED.voting_closes,
                predictions_close = EXCLUDED.predictions_close,
                status = EXCLUDED.status,
                voting_ruleset_version = 'v5',
                revote_ruleset_version = 'v6'
            RETURNING id
            """
        )
        show_id = cursor.fetchone()["id"]
        song_ids = []
        for country_id, entry_number, submitter_id, title in (
            ("US", 1, 2, "Home Entry"),
            ("ES", 1, 3, "Spanish Entry"),
            ("FR", 1, 3, "French Entry"),
            ("ES", 2, 3, "Second Spanish Entry"),
        ):
            cursor.execute(
                """
                INSERT INTO song (country_id, year_id, entry_number)
                VALUES (%s, 2025, %s)
                RETURNING id
                """,
                (country_id, entry_number),
            )
            song_id = cursor.fetchone()["id"]
            song_ids.append(song_id)
            cursor.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist
                   ) VALUES (%s, %s, %s, 'Artist')""",
                (song_id, submitter_id, title),
            )
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [(song_id, show_id, index) for index, song_id in enumerate(song_ids, start=1)],
        )
    db.commit()
    return song_ids


def _seed_official_ballot(db, song_ids):
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE show
            SET date = CURRENT_DATE,
                voting_closes = CURRENT_TIMESTAMP - INTERVAL '1 minute'
            WHERE year_id = 2025 AND short_name = 'f'
            RETURNING id
            """
        )
        show_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (2, %s, 'US', 'official')
            RETURNING id
            """,
            (show_id,),
        )
        vote_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_ids[1], 12),
                (vote_set_id, song_ids[2], 10),
                (vote_set_id, song_ids[3], 8),
            ],
        )
    db.commit()
    return show_id


def test_show_results_are_rebuilt_once_after_a_complete_ballot(db):
    song_ids = _seed_show_and_songs(db)
    refresh_notices = []

    def capture_refresh_notice(diagnostic):
        if diagnostic.message_primary.startswith("Refreshed official results"):
            refresh_notices.append(diagnostic.message_primary)

    db.add_notice_handler(capture_refresh_notice)
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            SELECT 2, show_id, 'US', 'official'
            FROM song_show
            WHERE song_id = %s
            RETURNING id, show_id
            """,
            (song_ids[0],),
        )
        ballot = cursor.fetchone()
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (ballot["id"], song_ids[1], 12),
                (ballot["id"], song_ids[2], 10),
                (ballot["id"], song_ids[3], 8),
            ],
        )

        # Results stay unchanged until the complete ballot transaction commits.
        cursor.execute(
            "SELECT SUM(total_points) AS points FROM country_show_results WHERE show_id = %s",
            (ballot["show_id"],),
        )
        assert cursor.fetchone()["points"] == 0

    db.commit()

    assert len(refresh_notices) == 1
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT country_id, total_points
            FROM country_show_results
            WHERE show_id = %s AND result_mode = 'official'
            ORDER BY total_points DESC
            """,
            (ballot["show_id"],),
        )
        assert [(row["country_id"], row["total_points"]) for row in cursor.fetchall()] == [
            ("ES", 12),
            ("FR", 10),
            ("ES", 8),
            ("US", 0),
        ]

        cursor.execute("SELECT COUNT(*) AS count FROM show_result_refresh_queue")
        assert cursor.fetchone()["count"] == 0

    refresh_notices.clear()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE vote_set SET nickname = 'Changed' WHERE id = %s",
            (ballot["id"],),
        )
        cursor.execute("DELETE FROM vote WHERE vote_set_id = %s", (ballot["id"],))
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (ballot["id"], song_ids[1], 8),
                (ballot["id"], song_ids[2], 12),
                (ballot["id"], song_ids[3], 10),
            ],
        )
    db.commit()

    assert len(refresh_notices) == 1
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT country_id, total_points
            FROM country_show_results
            WHERE show_id = %s AND result_mode = 'official'
            ORDER BY total_points DESC
            """,
            (ballot["show_id"],),
        )
        assert [(row["country_id"], row["total_points"]) for row in cursor.fetchall()] == [
            ("FR", 12),
            ("ES", 10),
            ("ES", 8),
            ("US", 0),
        ]


def test_show_result_entries_load_only_lineup_and_cached_results(app, db):
    song_ids = _seed_show_and_songs(db)
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            SELECT 2, show_id, 'US', 'official'
            FROM song_show
            WHERE song_id = %s
            RETURNING id
            """,
            (song_ids[0],),
        )
        vote_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_ids[1], 12),
                (vote_set_id, song_ids[2], 10),
                (vote_set_id, song_ids[3], 8),
            ],
        )
    db.commit()

    app.config["PERFORMANCE_HEADERS"] = True

    @app.get("/_test/show-song-loading")
    def show_song_loading():
        songs = get_show_result_entries(2025, "f")
        assert songs is not None
        return {
            "songs": [
                {
                    "id": song.id,
                    "title": song.title,
                    "lyrics": song.native_lyrics,
                    "points": song.vote_data.sum if song.vote_data else None,
                }
                for song in songs
            ]
        }

    response = app.test_client().get("/_test/show-song-loading")

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "4"
    songs = response.get_json()["songs"]
    assert len(songs) == 4
    assert all(song["title"] for song in songs)
    assert [song["lyrics"] for song in songs] == [None] * 4
    assert [song["points"] for song in songs] == [0, 12, 10, 8]


def test_show_song_loading_uses_materialized_results_without_ballots(app, db):
    _seed_show_and_songs(db)
    app.config["PERFORMANCE_HEADERS"] = True

    @app.get("/_test/show-song-loading-without-ballots")
    def show_song_loading_without_ballots():
        songs = get_show_result_entries(2025, "f")
        assert songs is not None
        return {"points": [song.vote_data.sum if song.vote_data else None for song in songs]}

    response = app.test_client().get("/_test/show-song-loading-without-ballots")

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "4"
    assert response.get_json()["points"] == [0, 0, 0, 0]


def test_year_index_loads_all_winners_in_one_bulk_operation(app, db):
    _seed_show_and_songs(db)
    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET status = 'open' WHERE id = 2024")
        cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2025")
        cursor.execute(
            "SELECT song_id FROM country_year_results WHERE year_id = 2025 AND place = 1"
        )
        expected_winner_id = cursor.fetchone()["song_id"]
    db.commit()

    app.config["PERFORMANCE_HEADERS"] = True

    @app.get("/_test/year-index-winners")
    def year_index_winners():
        winners = get_year_index_winners()
        return {
            "winners": {
                year: {
                    "id": song.id,
                    "title": song.title,
                    "points": song.vote_data.sum if song.vote_data else None,
                }
                for year, song in winners.items()
            }
        }

    try:
        loader_response = app.test_client().get("/_test/year-index-winners")
        index_response = app.test_client().get("/year", headers={"Accept": "text/html"})

        assert loader_response.status_code == 200
        assert loader_response.headers["X-SQL-Query-Count"] == "2"
        assert loader_response.get_json()["winners"]["2025"]["id"] == expected_winner_id

        assert index_response.status_code == 200
        assert index_response.headers["X-SQL-Query-Count"] == "3"
        assert "Done years" in index_response.text
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2024")
            cursor.execute("UPDATE year SET status = 'open' WHERE id = 2025")
        db.commit()


def test_detailed_results_fetch_the_vote_matrix_once(app, db):
    song_ids = _seed_show_and_songs(db)
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE show
            SET voting_closes = CURRENT_TIMESTAMP - INTERVAL '1 minute'
            WHERE year_id = 2025 AND short_name = 'f'
            """
        )
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            SELECT 2, id, 'US', 'official'
            FROM show
            WHERE year_id = 2025 AND short_name = 'f'
            RETURNING id
            """
        )
        vote_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_ids[1], 12),
                (vote_set_id, song_ids[2], 10),
                (vote_set_id, song_ids[3], 8),
            ],
        )
    db.commit()

    app.config["PERFORMANCE_HEADERS"] = True
    response = app.test_client().get("/year/2025/f/detailed", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "9"
    assert "bob" in response.text
    assert "12" in response.text


def test_user_vote_history_fetches_entries_and_redaction_metadata_once(app, db):
    song_ids = _seed_show_and_songs(db)
    _seed_official_ballot(db, song_ids)
    app.config["PERFORMANCE_HEADERS"] = True

    response = app.test_client().get("/user/bob/votes", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "3"
    assert "Spanish Entry" in response.text
    assert "12p" in response.text


def test_user_vote_history_redacts_from_bulk_membership_metadata(app, db):
    song_ids = _seed_show_and_songs(db)
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('partial') ON CONFLICT DO NOTHING")
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_type, show_number, status, date
            )
            VALUES (2025, 10, 'sf', 1, 'partial', CURRENT_DATE - 1)
            RETURNING id
            """
        )
        semifinal_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [
                (song_id, semifinal_id, position)
                for position, song_id in enumerate(song_ids, start=1)
            ],
        )
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (2, %s, 'US', 'official')
            RETURNING id
            """,
            (semifinal_id,),
        )
        vote_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_ids[1], 12),
                (vote_set_id, song_ids[2], 10),
                (vote_set_id, song_ids[3], 8),
            ],
        )
    db.commit()
    app.config["PERFORMANCE_HEADERS"] = True

    response = app.test_client().get("/user/bob/votes", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "3"
    assert "Spanish Entry" not in response.text
    assert response.text.count("f-qualifier") >= 3


def test_user_predictions_fetches_every_set_entry_once(app, db):
    song_ids = _seed_show_and_songs(db)
    show_id = _seed_official_ballot(db, song_ids)
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO prediction_set (user_id, show_id)
            VALUES (2, %s)
            RETURNING id
            """,
            (show_id,),
        )
        prediction_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO prediction (set_id, song_id, position) VALUES (%s, %s, %s)",
            [
                (prediction_set_id, song_id, position)
                for position, song_id in enumerate(song_ids, start=1)
            ],
        )
    db.commit()
    app.config["PERFORMANCE_HEADERS"] = True

    response = app.test_client().get("/user/bob/predictions", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "4"
    assert "Home Entry" in response.text
    assert "Spanish Entry" in response.text


def test_user_revote_history_fetches_all_metadata_once(app, db):
    song_ids = _seed_show_and_songs(db)
    show_id = _seed_official_ballot(db, song_ids)
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE show SET revote_eligible_at = CURRENT_TIMESTAMP WHERE id = %s",
            (show_id,),
        )
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (2, %s, 'US', 'revote')
            RETURNING id
            """,
            (show_id,),
        )
        vote_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_ids[1], 12),
                (vote_set_id, song_ids[2], 10),
                (vote_set_id, song_ids[3], 8),
            ],
        )
    db.commit()
    app.config["PERFORMANCE_HEADERS"] = True

    response = app.test_client().get("/user/bob/revotes", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "3"
    assert "Spanish Entry" in response.text
    assert "revotes-with-difference" in response.text


def test_scoreboard_fetches_owned_songs_once(app, db):
    song_ids = _seed_show_and_songs(db)
    _seed_official_ballot(db, song_ids)
    app.config["PERFORMANCE_HEADERS"] = True

    response = app.test_client().get("/year/2025/f/scoreboard/votes")

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "10"
    data = response.get_json()
    assert data["vote_order"] == ["bob"]
    assert song_ids[0] in data["user_songs"]["bob"]


class TestVotingApi:
    def test_ballot_requires_authentication(self, client):
        response = client.get("/api/voting/2025-f")
        assert response.status_code == 401

    def test_get_and_save_ballot(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)

        response = client.get("/api/voting/2025-f", headers=bob_headers)
        assert response.status_code == 200
        data = _result(response)
        assert data["ballot"] is None
        assert [song["id"] for song in data["songs"]] == song_ids
        assert data["countries"] == [{"id": "US", "name": "United States", "cc3": "USA"}]

        response = client.get("/api/voting/2025-f/countries", headers=bob_headers)
        assert response.status_code == 200
        assert _result(response) == data["countries"]

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "nickname": "Bob",
                "country_id": "US",
                "votes": [
                    {"score": 12, "song_id": song_ids[1]},
                    {"score": 10, "song_id": song_ids[2]},
                    {"score": 8, "song_id": song_ids[3]},
                ],
            },
        )
        assert response.status_code == 200
        ballot = _result(response)
        assert ballot["nickname"] == "Bob"
        assert ballot["country_id"] == "US"
        assert ballot["votes"] == [
            {"score": 12, "song_id": song_ids[1]},
            {"score": 10, "song_id": song_ids[2]},
            {"score": 8, "song_id": song_ids[3]},
        ]

        response = client.get("/api/voting/2025-f", headers=bob_headers)
        assert _result(response)["ballot"]["votes"] == ballot["votes"]

    def test_ballot_rejects_own_song_and_incomplete_scores(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)
        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "votes": [
                    {"score": 12, "song_id": song_ids[0]},
                    {"score": 10, "song_id": song_ids[1]},
                ],
            },
        )
        assert response.status_code == 400
        assert "each show score" in response.get_json()["error"]["description"]

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "votes": [
                    {"score": 12, "song_id": song_ids[0]},
                    {"score": 10, "song_id": song_ids[1]},
                    {"score": 8, "song_id": song_ids[2]},
                ],
            },
        )
        assert response.status_code == 400
        assert "own song" in response.get_json()["error"]["description"]

    def test_v2_uses_ballot_flag_and_allows_other_owned_entries(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE show
                SET voting_ruleset_version = 'v2'
                WHERE id = (SELECT show_id FROM song_show WHERE song_id = %s)
                """,
                (song_ids[0],),
            )
            from world_stage.utils.song_revisions import create_song_revision

            create_song_revision(cursor, song_ids[1], {"submitter_id": 2}, changed_by=None)
        db.commit()

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "country_id": "US",
                "votes": [
                    {"score": 12, "song_id": song_ids[1]},
                    {"score": 10, "song_id": song_ids[2]},
                    {"score": 8, "song_id": song_ids[3]},
                ],
            },
        )
        assert response.status_code == 200

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "country_id": "US",
                "votes": [
                    {"score": 12, "song_id": song_ids[0]},
                    {"score": 10, "song_id": song_ids[2]},
                    {"score": 8, "song_id": song_ids[3]},
                ],
            },
        )
        assert response.status_code == 400
        assert "voting flag" in response.get_json()["error"]["description"]

    def test_v1_requires_one_point_for_the_ballot_flag_entry(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)
        with db.cursor() as cursor:
            cursor.execute("UPDATE point SET score = 1 WHERE id = 103")
            cursor.execute(
                """
                UPDATE show
                SET voting_ruleset_version = 'v1'
                WHERE id = (SELECT show_id FROM song_show WHERE song_id = %s)
                """,
                (song_ids[0],),
            )
        db.commit()

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "country_id": "US",
                "votes": [
                    {"score": 12, "song_id": song_ids[0]},
                    {"score": 10, "song_id": song_ids[1]},
                    {"score": 1, "song_id": song_ids[2]},
                ],
            },
        )
        assert response.status_code == 400
        assert "must receive 1 point" in response.get_json()["error"]["description"]

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "country_id": "US",
                "votes": [
                    {"score": 12, "song_id": song_ids[1]},
                    {"score": 10, "song_id": song_ids[2]},
                    {"score": 1, "song_id": song_ids[0]},
                ],
            },
        )
        assert response.status_code == 200


class TestPredictionApi:
    def test_get_and_save_prediction(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)

        response = client.get("/api/voting/2025-f/prediction", headers=bob_headers)
        assert response.status_code == 200
        assert _result(response)["prediction"] is None
        assert _result(response)["prediction_count"] == 0

        response = client.put(
            "/api/voting/2025-f/prediction",
            headers=bob_headers,
            json={
                "predictions": [
                    {"song_id": song_ids[3], "position": 1},
                    {"song_id": song_ids[2], "position": 2},
                    {"song_id": song_ids[1], "position": 3},
                    {"song_id": song_ids[0], "position": 4},
                ]
            },
        )
        assert response.status_code == 200
        prediction = _result(response)
        assert prediction["predictions"][0] == {"song_id": song_ids[3], "position": 1}

        response = client.get("/api/voting/2025-f/prediction", headers=bob_headers)
        assert _result(response)["prediction_count"] == 1
        assert _result(response)["prediction"]["predictions"] == prediction["predictions"]

    def test_prediction_requires_a_complete_ranking(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)
        response = client.put(
            "/api/voting/2025-f/prediction",
            headers=bob_headers,
            json={"predictions": [{"song_id": song_ids[0], "position": 1}]},
        )
        assert response.status_code == 400
        assert "every song" in response.get_json()["error"]["description"]
