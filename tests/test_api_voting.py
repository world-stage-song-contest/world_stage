"""Tests for authenticated ballot and prediction API endpoints."""

from world_stage.utils import get_show_songs, get_show_winner, get_year_winner


def _result(response):
    return response.get_json()["result"]


def _seed_show_and_songs(db):
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (10, 1) ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """
            INSERT INTO point (id, point_system_id, place, score)
            VALUES (101, 10, 1, 12), (102, 10, 2, 10), (103, 10, 3, 8)
            ON CONFLICT DO NOTHING
            """
        )
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_name, short_name,
                voting_opens, voting_closes, predictions_close, status
            )
            VALUES (
                2025, 10, 'Final', 'f',
                CURRENT_TIMESTAMP - INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '30 minutes', 'full'
            )
            ON CONFLICT (year_id, show_name) DO UPDATE
            SET point_system_id = EXCLUDED.point_system_id,
                short_name = EXCLUDED.short_name,
                voting_opens = EXCLUDED.voting_opens,
                voting_closes = EXCLUDED.voting_closes,
                predictions_close = EXCLUDED.predictions_close,
                status = EXCLUDED.status
            RETURNING id
            """
        )
        show_id = cursor.fetchone()["id"]
        song_ids = []
        for country_id, entry_number, submitter_id, title in (
            ('US', 1, 2, 'Home Entry'),
            ('ES', 1, 3, 'Spanish Entry'),
            ('FR', 1, 3, 'French Entry'),
            ('ES', 2, 3, 'Second Spanish Entry'),
        ):
            cursor.execute(
                """
                INSERT INTO song (
                    country_id, year_id, submitter_id, title, artist, is_placeholder, entry_number
                )
                VALUES (%s, 2025, %s, %s, 'Artist', false, %s)
                RETURNING id
                """,
                (country_id, submitter_id, title, entry_number),
            )
            song_ids.append(cursor.fetchone()["id"])
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [
                (song_id, show_id, index)
                for index, song_id in enumerate(song_ids, start=1)
            ],
        )
    db.commit()
    return song_ids


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
        assert [
            (row["country_id"], row["total_points"]) for row in cursor.fetchall()
        ] == [("ES", 12), ("FR", 10), ("ES", 8), ("US", 0)]

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
        assert [
            (row["country_id"], row["total_points"]) for row in cursor.fetchall()
        ] == [("FR", 12), ("ES", 10), ("ES", 8), ("US", 0)]


def test_show_song_loading_batches_languages_and_vote_data(app, db):
    song_ids = _seed_show_and_songs(db)
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE song
            SET title_language_id = 20, native_language_id = 20
            WHERE id = ANY(%s)
            """,
            (song_ids,),
        )
        cursor.executemany(
            "INSERT INTO song_language (song_id, language_id, priority) VALUES (%s, %s, 0)",
            [(song_id, 20) for song_id in song_ids],
        )
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
        songs = get_show_songs(2025, "f", select_languages=True, select_votes=True)
        assert songs is not None
        return {
            "songs": [
                {
                    "id": song.id,
                    "language": song.title_lang.name,
                    "languages": [language.name for language in song.languages],
                    "points": song.vote_data.sum if song.vote_data else None,
                }
                for song in songs
            ]
        }

    response = app.test_client().get("/_test/show-song-loading")

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "5"
    songs = response.get_json()["songs"]
    assert [song["language"] for song in songs] == ["English"] * 4
    assert [song["languages"] for song in songs] == [["English"]] * 4
    assert [song["points"] for song in songs] == [0, 12, 10, 8]


def test_show_song_loading_uses_materialized_results_without_ballots(app, db):
    _seed_show_and_songs(db)
    app.config["PERFORMANCE_HEADERS"] = True

    @app.get("/_test/show-song-loading-without-ballots")
    def show_song_loading_without_ballots():
        songs = get_show_songs(2025, "f", select_votes=True)
        assert songs is not None
        return {"points": [song.vote_data.sum if song.vote_data else None for song in songs]}

    response = app.test_client().get("/_test/show-song-loading-without-ballots")

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "4"
    assert response.get_json()["points"] == [0, 0, 0, 0]


def test_winner_loading_hydrates_only_the_materialized_winner(app, db):
    song_ids = _seed_show_and_songs(db)
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO song_language (song_id, language_id, priority) VALUES (%s, 20, 0)",
            (song_ids[1],),
        )
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

    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2025")
    db.commit()

    app.config["PERFORMANCE_HEADERS"] = True

    @app.get("/_test/show-winner-loading")
    def show_winner_loading():
        winner = get_show_winner(2025, "f")
        assert winner is not None
        assert winner.vote_data is not None
        return {
            "id": winner.id,
            "languages": [lang.name for lang in winner.languages],
            "points": winner.vote_data.sum,
            "percentage": winner.vote_data.pct(),
        }

    @app.get("/_test/year-winner-loading")
    def year_winner_loading():
        winner = get_year_winner(2025)
        assert winner is not None
        assert winner.vote_data is not None
        return {
            "id": winner.id,
            "languages": [lang.name for lang in winner.languages],
            "points": winner.vote_data.sum,
            "percentage": winner.vote_data.pct(),
        }

    show_response = app.test_client().get("/_test/show-winner-loading")
    year_response = app.test_client().get("/_test/year-winner-loading")

    assert show_response.status_code == 200
    assert show_response.headers["X-SQL-Query-Count"] == "2"
    assert show_response.get_json() == {
        "id": song_ids[1],
        "languages": ["English"],
        "points": 12,
        "percentage": "100.00%",
    }
    assert year_response.status_code == 200
    assert year_response.headers["X-SQL-Query-Count"] == "2"
    assert year_response.get_json() == {
        "id": song_ids[1],
        "languages": ["English"],
        "points": 12,
        "percentage": "100.00%",
    }

    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET status = 'open' WHERE id = 2025")
    db.commit()


class TestVotingApi:
    def test_ballot_requires_authentication(self, client):
        response = client.get('/api/voting/2025-f')
        assert response.status_code == 401

    def test_get_and_save_ballot(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)

        response = client.get('/api/voting/2025-f', headers=bob_headers)
        assert response.status_code == 200
        data = _result(response)
        assert data['ballot'] is None
        assert [song['id'] for song in data['songs']] == song_ids
        assert data['countries'] == [{'id': 'US', 'name': 'United States', 'cc3': 'USA'}]

        response = client.get('/api/voting/2025-f/countries', headers=bob_headers)
        assert response.status_code == 200
        assert _result(response) == data['countries']

        response = client.put(
            '/api/voting/2025-f',
            headers=bob_headers,
            json={
                'nickname': 'Bob',
                'country_id': 'US',
                'votes': [
                    {'score': 12, 'song_id': song_ids[1]},
                    {'score': 10, 'song_id': song_ids[2]},
                    {'score': 8, 'song_id': song_ids[3]},
                ],
            },
        )
        assert response.status_code == 200
        ballot = _result(response)
        assert ballot['nickname'] == 'Bob'
        assert ballot['country_id'] == 'US'
        assert ballot['votes'] == [
            {'score': 12, 'song_id': song_ids[1]},
            {'score': 10, 'song_id': song_ids[2]},
            {'score': 8, 'song_id': song_ids[3]},
        ]

        response = client.get('/api/voting/2025-f', headers=bob_headers)
        assert _result(response)['ballot']['votes'] == ballot['votes']

    def test_ballot_rejects_own_song_and_incomplete_scores(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)
        response = client.put(
            '/api/voting/2025-f',
            headers=bob_headers,
            json={
                'votes': [
                    {'score': 12, 'song_id': song_ids[0]},
                    {'score': 10, 'song_id': song_ids[1]},
                ],
            },
        )
        assert response.status_code == 400
        assert 'each show score' in response.get_json()['error']['description']

        response = client.put(
            '/api/voting/2025-f',
            headers=bob_headers,
            json={
                'votes': [
                    {'score': 12, 'song_id': song_ids[0]},
                    {'score': 10, 'song_id': song_ids[1]},
                    {'score': 8, 'song_id': song_ids[2]},
                ],
            },
        )
        assert response.status_code == 400
        assert 'own song' in response.get_json()['error']['description']


class TestPredictionApi:
    def test_get_and_save_prediction(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)

        response = client.get('/api/voting/2025-f/prediction', headers=bob_headers)
        assert response.status_code == 200
        assert _result(response)['prediction'] is None
        assert _result(response)['prediction_count'] == 0

        response = client.put(
            '/api/voting/2025-f/prediction',
            headers=bob_headers,
            json={
                'predictions': [
                    {'song_id': song_ids[3], 'position': 1},
                    {'song_id': song_ids[2], 'position': 2},
                    {'song_id': song_ids[1], 'position': 3},
                    {'song_id': song_ids[0], 'position': 4},
                ]
            },
        )
        assert response.status_code == 200
        prediction = _result(response)
        assert prediction['predictions'][0] == {'song_id': song_ids[3], 'position': 1}

        response = client.get('/api/voting/2025-f/prediction', headers=bob_headers)
        assert _result(response)['prediction_count'] == 1
        assert _result(response)['prediction']['predictions'] == prediction['predictions']

    def test_prediction_requires_a_complete_ranking(self, client, db, bob_headers):
        song_ids = _seed_show_and_songs(db)
        response = client.put(
            '/api/voting/2025-f/prediction',
            headers=bob_headers,
            json={'predictions': [{'song_id': song_ids[0], 'position': 1}]},
        )
        assert response.status_code == 400
        assert 'every song' in response.get_json()['error']['description']
