"""Regression coverage for taste-similarity ballot selection."""

import pytest


def _insert_ballot(cursor, voter_id, show_id, result_mode, song_ids, scores):
    cursor.execute(
        """
        INSERT INTO vote_set (voter_id, show_id, result_mode)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (voter_id, show_id, result_mode),
    )
    vote_set_id = cursor.fetchone()["id"]
    cursor.executemany(
        "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
        [
            (vote_set_id, song_id, score)
            for song_id, score in zip(song_ids, scores, strict=True)
        ],
    )


def _delete_ballot(cursor, voter_id, show_id, result_mode):
    cursor.execute(
        """
        DELETE FROM vote
        WHERE vote_set_id IN (
            SELECT id FROM vote_set
            WHERE voter_id = %s AND show_id = %s AND result_mode = %s
        )
        """,
        (voter_id, show_id, result_mode),
    )
    cursor.execute(
        """
        DELETE FROM vote_set
        WHERE voter_id = %s AND show_id = %s AND result_mode = %s
        """,
        (voter_id, show_id, result_mode),
    )


def _similarity(cursor, include_revotes=None):
    if include_revotes is None:
        cursor.execute(
            """
            SELECT similarity
            FROM user_taste_similarity(1, 2024, 2024, false)
            WHERE other_id = 2
            """
        )
    else:
        cursor.execute(
            """
            SELECT similarity
            FROM user_taste_similarity(1, 2024, 2024, false, %s)
            WHERE other_id = 2
            """,
            (include_revotes,),
        )
    return float(cursor.fetchone()["similarity"])


def test_taste_similarity_revotes_replace_each_voters_official_ballot(client, db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point_system")
        point_system_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 3)", (point_system_id,)
        )
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point")
        first_point_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO point (id, point_system_id, place, score) VALUES (%s, %s, %s, %s)",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
                (first_point_id + 2, point_system_id, 3, 8),
            ],
        )
        cursor.execute(
            """
            INSERT INTO show (year_id, point_system_id, show_name, short_name, status, dtf)
            VALUES (2024, %s, 'Taste similarity test', 'taste', 'full', 1)
            RETURNING id
            """,
            (point_system_id,),
        )
        show_id = cursor.fetchone()["id"]

        song_ids = []
        for country, title in (
            ("US", "Taste one"),
            ("ES", "Taste two"),
            ("FR", "Taste three"),
        ):
            cursor.execute(
                """
                INSERT INTO song (country_id, year_id, title, artist, is_placeholder)
                VALUES (%s, 2024, %s, 'Artist', false)
                RETURNING id
                """,
                (country, title),
            )
            song_ids.append(cursor.fetchone()["id"])
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [
                (song_id, show_id, running_order)
                for running_order, song_id in enumerate(song_ids, start=1)
            ],
        )

        _insert_ballot(cursor, 1, show_id, "official", song_ids, [12, 10, 8])
        _insert_ballot(cursor, 2, show_id, "official", song_ids, [12, 10, 8])
        _insert_ballot(cursor, 1, show_id, "revote", song_ids, [8, 10, 12])
        _insert_ballot(cursor, 2, show_id, "revote", song_ids, [12, 8, 10])
    db.commit()

    # Omitting the new argument defaults to both Revote ballots. Disabling it
    # ignores both Revotes and compares the matching official ballots.
    with db.cursor() as cursor:
        assert _similarity(cursor) == pytest.approx(-0.5)
        assert _similarity(cursor, include_revotes=False) == pytest.approx(1.0)

    response = client.get("/user/alice/similar", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b'name="include_revotes" value="true" checked' in response.data
    assert b"-50.00%" in response.data

    response = client.get(
        "/user/alice/similar?include_revotes=false", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    assert b'name="include_revotes" value="true" checked' not in response.data
    assert b"100.00%" in response.data

    # An unchecked form checkbox is absent from the query string; the form's
    # submission sentinel distinguishes that from the default first visit.
    response = client.get(
        "/user/alice/similar?_submitted=1&include_specials=true",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 200
    assert b'name="include_revotes" value="true" checked' not in response.data
    assert b"100.00%" in response.data

    with db.cursor() as cursor:
        # Alice's Revote combines with Bob's official ballot when Bob has not
        # revoted, rather than dropping the show or using Alice's official.
        _delete_ballot(cursor, 2, show_id, "revote")
        db.commit()
        assert _similarity(cursor) == pytest.approx(-1.0)

        # The opposite mixed pair independently selects Bob's Revote and
        # Alice's official ballot.
        _insert_ballot(cursor, 2, show_id, "revote", song_ids, [12, 8, 10])
        _delete_ballot(cursor, 1, show_id, "revote")
        db.commit()
        assert _similarity(cursor) == pytest.approx(0.5)

        # With no Revotes, include_revotes naturally falls back to both
        # official ballots.
        _delete_ballot(cursor, 2, show_id, "revote")
        db.commit()
        assert _similarity(cursor) == pytest.approx(1.0)
