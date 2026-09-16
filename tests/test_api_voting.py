import string

from hypothesis import given, settings
from hypothesis import strategies as st


def _seed_show_and_songs(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute(
            """INSERT INTO point_system (id, metadata)
               VALUES (10, '{"points": [12, 10, 8]}') ON CONFLICT DO NOTHING"""
        )
        show_id = cursor.execute(
            """INSERT INTO show (
                   year_id, point_system_id, show_type,
                   voting_opens, voting_closes, predictions_close, status
               ) VALUES (
                   2025, 10, 'f',
                   CURRENT_TIMESTAMP - INTERVAL '1 hour',
                   CURRENT_TIMESTAMP + INTERVAL '1 hour',
                   CURRENT_TIMESTAMP + INTERVAL '30 minutes', 'full'
               )
               ON CONFLICT (year_id, short_name)
                   WHERE national_final_id IS NULL DO UPDATE
               SET point_system_id = EXCLUDED.point_system_id,
                   voting_opens = EXCLUDED.voting_opens,
                   voting_closes = EXCLUDED.voting_closes,
                   predictions_close = EXCLUDED.predictions_close,
                   status = EXCLUDED.status,
                   voting_ruleset_version = 'v5',
                   revote_ruleset_version = 'v6'
               RETURNING id"""
        ).fetchone()["id"]
        songs = []
        for country, entry_number, submitter, title in (
            ("US", 1, 2, "Home Entry"),
            ("ES", 1, 3, "Spanish Entry"),
            ("FR", 1, 3, "French Entry"),
            ("ES", 2, 3, "Second Spanish Entry"),
        ):
            song_id = cursor.execute(
                """INSERT INTO song (country_id, year_id, entry_number)
                   VALUES (%s, 2025, %s) RETURNING id""",
                (country, entry_number),
            ).fetchone()["id"]
            cursor.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist_credit_set_id
                   ) VALUES (%s, %s, %s, test_artist_credit('Artist'))""",
                (song_id, submitter, title),
            )
            songs.append(song_id)
        cursor.executemany(
            """INSERT INTO song_show (song_id, show_id, running_order)
               VALUES (%s, %s, %s)""",
            [(song_id, show_id, position) for position, song_id in enumerate(songs, start=1)],
        )
    db.commit()
    return show_id, songs


def test_voting_api_requires_authentication(client):
    @given(
        request=st.sampled_from(
            [
                ("get", "/api/voting/2025-f"),
                ("put", "/api/voting/2025-f"),
                ("get", "/api/voting/2025-f/countries"),
                ("get", "/api/voting/2025-f/prediction"),
                ("put", "/api/voting/2025-f/prediction"),
            ]
        )
    )
    def property_test(request):
        method, path = request
        response = getattr(client, method)(path, json={} if method == "put" else None)
        assert response.status_code == 401

    property_test()


def test_ballots_round_trip_and_materialize_points_for_any_valid_order(client, db, bob_headers):
    show_id, songs = _seed_show_and_songs(db)
    eligible = songs[1:]
    scores = [12, 10, 8]

    @settings(max_examples=10, deadline=None)
    @given(
        order=st.permutations(eligible),
        nickname=st.text(
            alphabet=string.ascii_letters + string.digits + " -_",
            min_size=0,
            max_size=30,
        ),
    )
    def property_test(order, nickname):
        votes = [
            {"score": score, "song_id": song_id}
            for score, song_id in zip(scores, order, strict=True)
        ]
        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={"nickname": nickname, "country_id": "US", "votes": votes},
        )
        assert response.status_code == 200
        ballot = response.get_json()["result"]
        assert ballot["nickname"] == (None if nickname == "" else nickname.strip())
        assert ballot["country_id"] == "US"
        assert ballot["votes"] == votes

        loaded = client.get("/api/voting/2025-f", headers=bob_headers).get_json()["result"]
        assert loaded["ballot"]["votes"] == votes
        points = {
            row["song_id"]: row["total_points"]
            for row in db.execute(
                """SELECT song_id, total_points FROM country_show_results
                   WHERE show_id = %s AND result_mode = 'official'""",
                (show_id,),
            ).fetchall()
        }
        assert points == {
            **{song_id: score for song_id, score in zip(order, scores, strict=True)},
            songs[0]: 0,
        }

    property_test()


def test_invalid_ballots_are_rejected_without_replacing_the_saved_ballot(client, db, bob_headers):
    _, songs = _seed_show_and_songs(db)
    valid_votes = [
        {"score": score, "song_id": song_id}
        for score, song_id in zip([12, 10, 8], songs[1:], strict=True)
    ]
    assert (
        client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={"country_id": "US", "votes": valid_votes},
        ).status_code
        == 200
    )

    @given(kind=st.sampled_from(["incomplete", "owned", "duplicate-score", "unknown-song"]))
    def property_test(kind):
        if kind == "incomplete":
            invalid = valid_votes[:-1]
        elif kind == "owned":
            invalid = [{**valid_votes[0], "song_id": songs[0]}, *valid_votes[1:]]
        elif kind == "duplicate-score":
            invalid = [{**valid_votes[0], "score": 10}, *valid_votes[1:]]
        else:
            invalid = [{**valid_votes[0], "song_id": max(songs) + 10_000}, *valid_votes[1:]]

        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={"country_id": "US", "votes": invalid},
        )
        assert response.status_code == 400
        loaded = client.get("/api/voting/2025-f", headers=bob_headers).get_json()["result"]
        assert loaded["ballot"]["votes"] == valid_votes

    property_test()


def test_predictions_round_trip_for_every_ranking_and_reject_partial_rankings(
    client, db, bob_headers
):
    _, songs = _seed_show_and_songs(db)

    @settings(max_examples=10, deadline=None)
    @given(order=st.permutations(songs), complete=st.booleans())
    def property_test(order, complete):
        before = client.get("/api/voting/2025-f/prediction", headers=bob_headers).get_json()[
            "result"
        ]["prediction"]
        selected = order if complete else order[:-1]
        predictions = [
            {"song_id": song_id, "position": position}
            for position, song_id in enumerate(selected, start=1)
        ]
        response = client.put(
            "/api/voting/2025-f/prediction",
            headers=bob_headers,
            json={"predictions": predictions},
        )
        assert response.status_code == (200 if complete else 400)
        loaded = client.get("/api/voting/2025-f/prediction", headers=bob_headers).get_json()[
            "result"
        ]["prediction"]
        if complete:
            assert loaded["predictions"] == predictions
        else:
            assert loaded == before

    property_test()
