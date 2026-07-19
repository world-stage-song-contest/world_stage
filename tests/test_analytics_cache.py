"""Regression coverage for the per-show analytics caches."""

from decimal import Decimal


def _insert_ballot(cursor, voter_id, show_id, mode, scores):
    cursor.execute(
        """
        INSERT INTO vote_set (voter_id, show_id, result_mode)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (voter_id, show_id, mode),
    )
    vote_set_id = cursor.fetchone()["id"]
    cursor.executemany(
        "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
        [(vote_set_id, song_id, score) for song_id, score in scores.items()],
    )


def _rows(cursor, query, params):
    cursor.execute(query, params)
    return cursor.fetchall()


def test_bias_caches_refresh_on_publication_revotes_and_metadata_changes(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('draw') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point_system")
        point_system_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 2)", (point_system_id,)
        )
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM point")
        first_point_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO point (id, point_system_id, place, score) VALUES (%s, %s, %s, %s)",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
            ],
        )
        cursor.execute(
            """
            INSERT INTO show (year_id, point_system_id, show_name, short_name, status, dtf)
            VALUES (2024, %s, 'Analytics cache test', 'analytics', 'draw', 1)
            RETURNING id
            """,
            (point_system_id,),
        )
        show_id = cursor.fetchone()["id"]

        song_ids = {}
        for submitter_id, country_id, title in (
            (1, "US", "Alice entry"),
            (2, "ES", "Bob entry"),
            (3, "FR", "Carol entry"),
        ):
            cursor.execute(
                """
                INSERT INTO song (
                    submitter_id, country_id, year_id, title, artist, is_placeholder
                )
                VALUES (%s, %s, 2024, %s, 'Artist', false)
                RETURNING id
                """,
                (submitter_id, country_id, title),
            )
            song_ids[submitter_id] = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [
                (song_id, show_id, running_order)
                for running_order, song_id in enumerate(song_ids.values(), start=1)
            ],
        )

        _insert_ballot(
            cursor,
            1,
            show_id,
            "official",
            {song_ids[2]: 12, song_ids[3]: 10},
        )
        _insert_ballot(
            cursor,
            2,
            show_id,
            "official",
            {song_ids[1]: 10, song_ids[3]: 12},
        )
        _insert_ballot(
            cursor,
            3,
            show_id,
            "official",
            {song_ids[1]: 12, song_ids[2]: 10},
        )
    db.commit()

    # Ballot writes do not populate analytics before the show is published.
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM country_bias_show_cache WHERE show_id = %s",
            (show_id,),
        )
        assert cursor.fetchone()["count"] == 0

        cursor.execute("UPDATE show SET status = 'full' WHERE id = %s", (show_id,))
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT ballot_mode, COUNT(*) AS count
            FROM country_bias_show_cache
            WHERE show_id = %s
            GROUP BY ballot_mode
            ORDER BY ballot_mode
            """,
            (show_id,),
        )
        assert [(row["ballot_mode"], row["count"]) for row in cursor] == [
            ("effective", 6),
            ("official", 6),
        ]

        submitter_rows = _rows(
            cursor,
            "SELECT * FROM user_submitter_bias(1, NULL, NULL, true, false)",
            (),
        )
        by_submitter = {row["submitter_id"]: row for row in submitter_rows}
        assert by_submitter[2]["given"] == 12
        assert by_submitter[2]["expected"] == Decimal("10.00")
        assert by_submitter[2]["received"] == 10
        assert by_submitter[2]["deficit"] == 2
        assert by_submitter[2]["votings_max"] == 1
        assert by_submitter[3]["given"] == 10
        assert by_submitter[3]["expected"] == Decimal("12.00")
        assert by_submitter[3]["received"] == 12
        assert by_submitter[3]["deficit"] == -2

        # Forward and inverse pages read opposite indexes of the same matrix.
        inverse_submitter = _rows(
            cursor,
            "SELECT * FROM submitter_voter_bias(2, NULL, NULL, true, false)",
            (),
        )
        alice_for_bob = next(row for row in inverse_submitter if row["voter_id"] == 1)
        assert alice_for_bob["given"] == by_submitter[2]["given"]
        assert alice_for_bob["expected"] == by_submitter[2]["expected"]
        assert alice_for_bob["received"] == by_submitter[2]["received"]

        country_rows = _rows(
            cursor,
            "SELECT * FROM user_country_bias(1, NULL, NULL, false)",
            (),
        )
        by_country = {row["country_id"]: row for row in country_rows}
        assert by_country["ES"]["given"] == 12
        assert by_country["ES"]["expected"] == Decimal("10.00")
        assert by_country["FR"]["given"] == 10
        assert by_country["FR"]["expected"] == Decimal("12.00")

        inverse_country = _rows(
            cursor,
            "SELECT * FROM country_voter_bias('ES', NULL, NULL, false)",
            (),
        )
        alice_for_es = next(row for row in inverse_country if row["voter_id"] == 1)
        assert alice_for_es["given"] == by_country["ES"]["given"]
        assert alice_for_es["expected"] == by_country["ES"]["expected"]

        assert _rows(
            cursor,
            "SELECT * FROM user_submitter_bias(1, 2025, 2025, true, false)",
            (),
        ) == []

        cursor.execute(
            "EXPLAIN (COSTS OFF) SELECT * FROM user_submitter_bias(1, NULL, NULL, true, false)"
        )
        plan = "\n".join(row["QUERY PLAN"] for row in cursor)
        assert "submitter_bias_show_cache" in plan
        assert "vote_set" not in plan

        # Metadata corrections invalidate both modes for the affected show.
        cursor.execute(
            "UPDATE song SET country_id = 'FR', entry_number = 2 WHERE id = %s",
            (song_ids[2],),
        )
    db.commit()
    with db.cursor() as cursor:
        country_rows = _rows(
            cursor,
            "SELECT * FROM user_country_bias(1, NULL, NULL, false)",
            (),
        )
        assert {row["country_id"] for row in country_rows} == {"FR"}
        cursor.execute(
            "UPDATE song SET country_id = 'ES', entry_number = 1 WHERE id = %s",
            (song_ids[2],),
        )
    db.commit()

    with db.cursor() as cursor:
        _insert_ballot(
            cursor,
            1,
            show_id,
            "revote",
            {song_ids[2]: 10, song_ids[3]: 12},
        )
    db.commit()

    with db.cursor() as cursor:
        effective_rows = _rows(
            cursor,
            "SELECT * FROM user_submitter_bias(1, NULL, NULL, true, true)",
            (),
        )
        effective = {row["submitter_id"]: row for row in effective_rows}
        assert effective[2]["given"] == 10
        assert effective[3]["given"] == 12

        official_rows = _rows(
            cursor,
            "SELECT * FROM user_submitter_bias(1, NULL, NULL, true, false)",
            (),
        )
        official = {row["submitter_id"]: row for row in official_rows}
        assert official[2]["given"] == 12
        assert official[3]["given"] == 10

        cursor.execute("UPDATE show SET status = 'draw' WHERE id = %s", (show_id,))
    db.commit()

    with db.cursor() as cursor:
        for table in (
            "taste_similarity_show_cache",
            "country_bias_show_cache",
            "submitter_bias_show_cache",
        ):
            cursor.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE show_id = %s", (show_id,))
            assert cursor.fetchone()["count"] == 0
