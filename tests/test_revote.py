from hypothesis import given
from hypothesis import strategies as st


def _insert_ballot(cursor, voter_id, show_id, mode, scores):
    vote_set_id = cursor.execute(
        """INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
           VALUES (%s, %s, 'FR', %s) RETURNING id""",
        (voter_id, show_id, mode),
    ).fetchone()["id"]
    cursor.executemany(
        "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
        [(vote_set_id, song_id, score) for song_id, score in scores.items()],
    )


def _results(db, show_id, mode):
    return {
        row["country_id"]: row["total_points"]
        for row in db.execute(
            """SELECT country_id, total_points
               FROM country_show_results
               WHERE show_id = %s AND result_mode = %s""",
            (show_id, mode),
        ).fetchall()
    }


def test_revotes_replace_effective_ballots_without_changing_official_results(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        point_system_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point_system"
        ).fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 1)",
            (point_system_id,),
        )
        first_point_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point"
        ).fetchone()["id"]
        cursor.executemany(
            """INSERT INTO point (id, point_system_id, place, score)
               VALUES (%s, %s, %s, %s)""",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
            ],
        )
        show_id = cursor.execute(
            """INSERT INTO show (year_id, point_system_id, show_type, status)
               VALUES (2024, %s, 'sf', 'full') RETURNING id""",
            (point_system_id,),
        ).fetchone()["id"]

        songs = {}
        for position, country in enumerate(("US", "ES"), start=1):
            song_id = cursor.execute(
                """INSERT INTO song (country_id, year_id, entry_number)
                   VALUES (%s, 2024, %s) RETURNING id""",
                (country, position),
            ).fetchone()["id"]
            cursor.execute(
                """INSERT INTO song_data (song_id, title, artist_credit_set_id)
                   VALUES (%s, %s, test_artist_credit('Artist'))""",
                (song_id, f"{country} entry"),
            )
            cursor.execute(
                """INSERT INTO song_show (song_id, show_id, running_order)
                   VALUES (%s, %s, %s)""",
                (song_id, show_id, position),
            )
            songs[country] = song_id

        official = {songs["US"]: 12, songs["ES"]: 10}
        _insert_ballot(cursor, 2, show_id, "official", official)
    db.commit()
    official_results = {"US": 12, "ES": 10}

    @given(revote_scores=st.permutations([12, 10]))
    def property_test(revote_scores):
        db.rollback()
        db.execute(
            """DELETE FROM vote
               WHERE vote_set_id IN (
                   SELECT id FROM vote_set
                   WHERE show_id = %s AND result_mode = 'revote'
               )""",
            (show_id,),
        )
        db.execute(
            "DELETE FROM vote_set WHERE show_id = %s AND result_mode = 'revote'",
            (show_id,),
        )
        with db.cursor() as cursor:
            _insert_ballot(
                cursor,
                2,
                show_id,
                "revote",
                dict(zip(songs.values(), revote_scores, strict=True)),
            )
        db.commit()

        assert _results(db, show_id, "official") == official_results
        assert _results(db, show_id, "revote") == dict(zip(songs, revote_scores, strict=True))

    property_test()
