import math

import pytest
from hypothesis import given
from hypothesis import strategies as st


def _insert_ballot(cursor, voter_id, show_id, result_mode, song_ids, scores):
    vote_set_id = cursor.execute(
        """INSERT INTO vote_set (voter_id, show_id, result_mode)
           VALUES (%s, %s, %s) RETURNING id""",
        (voter_id, show_id, result_mode),
    ).fetchone()["id"]
    cursor.executemany(
        "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
        [(vote_set_id, song_id, score) for song_id, score in zip(song_ids, scores, strict=True)],
    )


def _delete_revotes(cursor, show_id):
    cursor.execute(
        """DELETE FROM vote
           WHERE vote_set_id IN (
               SELECT id FROM vote_set
               WHERE show_id = %s AND result_mode = 'revote'
           )""",
        (show_id,),
    )
    cursor.execute(
        "DELETE FROM vote_set WHERE show_id = %s AND result_mode = 'revote'",
        (show_id,),
    )


def _correlation(left, right):
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator


def test_taste_similarity_uses_each_voters_effective_ballot(db):
    official = [12, 10, 8]
    alice_revote = [8, 10, 12]
    bob_revote = [12, 8, 10]
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        point_system_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point_system"
        ).fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 3)",
            (point_system_id,),
        )
        first_point_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point"
        ).fetchone()["id"]
        cursor.executemany(
            "INSERT INTO point (id, point_system_id, place, score) VALUES (%s, %s, %s, %s)",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
                (first_point_id + 2, point_system_id, 3, 8),
            ],
        )
        show_id = cursor.execute(
            """INSERT INTO show (
                   year_id, point_system_id, show_type, show_number, status
               ) VALUES (2024, %s, 'sf', 83, 'full') RETURNING id""",
            (point_system_id,),
        ).fetchone()["id"]
        song_ids = []
        for country in ("US", "ES", "FR"):
            song_id = cursor.execute(
                "INSERT INTO song (country_id, year_id) VALUES (%s, 2024) RETURNING id",
                (country,),
            ).fetchone()["id"]
            song_ids.append(song_id)
            cursor.execute(
                """INSERT INTO song_data (song_id, title, artist_credit_set_id)
                   VALUES (%s, %s, test_artist_credit('Artist'))""",
                (song_id, f"Taste {country}"),
            )
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [(song_id, show_id, position) for position, song_id in enumerate(song_ids, start=1)],
        )
        _insert_ballot(cursor, 1, show_id, "official", song_ids, official)
        _insert_ballot(cursor, 2, show_id, "official", song_ids, official)
    db.commit()

    @given(
        alice_has_revote=st.booleans(),
        bob_has_revote=st.booleans(),
        include_revotes=st.booleans(),
    )
    def property_test(alice_has_revote, bob_has_revote, include_revotes):
        with db.cursor() as cursor:
            _delete_revotes(cursor, show_id)
            if alice_has_revote:
                _insert_ballot(cursor, 1, show_id, "revote", song_ids, alice_revote)
            if bob_has_revote:
                _insert_ballot(cursor, 2, show_id, "revote", song_ids, bob_revote)
        db.commit()

        row = db.execute(
            """SELECT similarity
               FROM user_taste_similarity(1, 2024, 2024, false, %s)
               WHERE other_id = 2""",
            (include_revotes,),
        ).fetchone()
        alice_effective = alice_revote if include_revotes and alice_has_revote else official
        bob_effective = bob_revote if include_revotes and bob_has_revote else official
        assert float(row["similarity"]) == pytest.approx(
            _correlation(alice_effective, bob_effective)
        )

    property_test()
