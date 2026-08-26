from hypothesis import given, settings
from hypothesis import strategies as st


def _add_ballots(cursor, show_id, song_ids, score_orders):
    for voter_id, scores in zip((1, 2, 3), score_orders, strict=True):
        vote_set_id = cursor.execute(
            """INSERT INTO vote_set (voter_id, show_id, result_mode)
               VALUES (%s, %s, 'official') RETURNING id""",
            (voter_id, show_id),
        ).fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_id, score)
                for song_id, score in zip(song_ids, scores, strict=True)
            ],
        )


def _bias_reports(cursor, include_revotes=False):
    queries = (
        (
            """SELECT * FROM user_country_bias(1, 2024, 2024, %s)
            WHERE country_id = 'ES'""",
            (include_revotes,),
        ),
        (
            """SELECT * FROM country_voter_bias('ES', 2024, 2024, %s)
            WHERE voter_id = 1""",
            (include_revotes,),
        ),
        (
            """SELECT * FROM user_submitter_bias(1, 2024, 2024, false, %s)
            WHERE submitter_id = 4""",
            (include_revotes,),
        ),
        (
            """SELECT * FROM submitter_voter_bias(4, 2024, 2024, false, %s)
            WHERE voter_id = 1""",
            (include_revotes,),
        ),
    )
    return tuple(dict(cursor.execute(query, params).fetchone()) for query, params in queries)


def test_national_final_ballots_never_change_bias_reports(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute(
            """INSERT INTO account (
                   id, username, email, password, salt, approved, role
               ) VALUES
                   (4, 'dave', 'dave@test', '\\x00', '\\x00', true, 'user'),
                   (5, 'eve', 'eve@test', '\\x00', '\\x00', true, 'user')
               ON CONFLICT DO NOTHING"""
        )
        point_system_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 2000 AS id FROM point_system"
        ).fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 3)",
            (point_system_id,),
        )
        first_point_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 2000 AS id FROM point"
        ).fetchone()["id"]
        cursor.executemany(
            """INSERT INTO point (id, point_system_id, place, score)
               VALUES (%s, %s, %s, %s)""",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
                (first_point_id + 2, point_system_id, 3, 8),
            ],
        )
        national_final_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 2000 AS id FROM national_final"
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO national_final (
                   id, year_id, owner_id, owner_country_id, short_name, name
               ) VALUES (%s, 2024, 4, 'FR', %s, 'Bias Test Final')""",
            (national_final_id, f"bias-{national_final_id}"),
        )
        show_ids = []
        for national_final in (None, national_final_id):
            show_ids.append(
                cursor.execute(
                    """INSERT INTO show (
                           year_id, point_system_id, show_type, status,
                           national_final_id
                       ) VALUES (2024, %s, 'f', 'full', %s)
                       RETURNING id""",
                    (point_system_id, national_final),
                ).fetchone()["id"]
            )

        show_songs = []
        for show_index, show_id in enumerate(show_ids):
            song_ids = []
            for song_index, (country_id, submitter_id) in enumerate(
                (("ES", 4), ("FR", 5), ("ES", 4)), start=1
            ):
                song_id = cursor.execute(
                    """INSERT INTO song (
                           country_id, year_id, entry_number, main_participant
                       ) VALUES (%s, 2024, %s, %s) RETURNING id""",
                    (country_id, show_index * 10 + song_index, show_index == 0),
                ).fetchone()["id"]
                cursor.execute(
                    """INSERT INTO song_data (
                           song_id, submitter_id, title, artist_credit_set_id
                       ) VALUES (%s, %s, %s, test_artist_credit('Bias Artist'))""",
                    (song_id, submitter_id, f"Bias song {song_id}"),
                )
                cursor.execute(
                    """INSERT INTO song_show (song_id, show_id, running_order)
                       VALUES (%s, %s, %s)""",
                    (song_id, show_id, song_index),
                )
                if show_index == 1:
                    cursor.execute(
                        """INSERT INTO national_final_song (national_final_id, song_id)
                           VALUES (%s, %s)""",
                        (national_final_id, song_id),
                    )
                song_ids.append(song_id)
            show_songs.append(song_ids)

        _add_ballots(
            cursor,
            show_ids[0],
            show_songs[0],
            ((12, 10, 8), (8, 12, 10), (10, 8, 12)),
        )
    db.commit()

    with db.cursor() as cursor:
        baseline = _bias_reports(cursor)

    score_orders = st.lists(st.permutations((12, 10, 8)), min_size=3, max_size=3)

    @settings(max_examples=8, deadline=None)
    @given(score_orders=score_orders, include_revotes=st.booleans())
    def property_test(score_orders, include_revotes):
        national_show_id = show_ids[1]
        with db.cursor() as cursor:
            cursor.execute(
                """DELETE FROM vote
                   WHERE vote_set_id IN (
                       SELECT id FROM vote_set WHERE show_id = %s
                   )""",
                (national_show_id,),
            )
            cursor.execute("DELETE FROM vote_set WHERE show_id = %s", (national_show_id,))
            _add_ballots(cursor, national_show_id, show_songs[1], score_orders)
        db.commit()

        with db.cursor() as cursor:
            reports = _bias_reports(cursor, include_revotes)
        assert reports == baseline

    property_test()
