import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st


@pytest.fixture()
def draw_setup(db, client):
    session_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO show_status (name)
            VALUES ('none'), ('draw'), ('partial'), ('full')
            ON CONFLICT DO NOTHING
        """)
        cur.execute("""
            INSERT INTO country (id, name, is_participating, cc3, pot)
            VALUES ('DE', 'Germany', true, 'DEU', 2)
            ON CONFLICT (id) DO UPDATE SET pot = EXCLUDED.pot
        """)
        cur.execute("UPDATE country SET pot = 1 WHERE id IN ('US', 'ES')")
        cur.execute("UPDATE country SET pot = 2 WHERE id = 'FR'")
        cur.execute(
            "UPDATE country SET semifinal_constraints = NULL WHERE id IN ('US', 'ES', 'FR', 'DE')"
        )
        cur.execute("""
            INSERT INTO show (year_id, show_type, show_number, status)
            VALUES (2025, 'sf', 1, 'draw'),
                   (2025, 'sf', 2, 'draw')
            ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL DO UPDATE
            SET status = EXCLUDED.status
        """)
        cur.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (1, %s, CURRENT_TIMESTAMP + '1 day')
            """,
            (session_id,),
        )

        song_ids = {}
        for cc, submitter in (("US", 1), ("ES", 2), ("FR", 3), ("DE", 1)):
            cur.execute(
                """
                INSERT INTO song (country_id, year_id)
                VALUES (%s, 2025)
                RETURNING id
                """,
                (cc,),
            )
            song_ids[cc] = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist_credit_set_id
                   ) VALUES (%s, %s, %s, test_artist_credit('Artist'))""",
                (song_ids[cc], submitter, f"{cc} Song"),
            )

    db.commit()
    client.set_cookie("session", session_id)

    yield song_ids

    with db.cursor() as cur:
        cur.execute("DELETE FROM song_show")
        cur.execute(
            """DELETE FROM show_progression
               WHERE source_show_id IN (SELECT id FROM show WHERE year_id = 2025)
                  OR target_show_id IN (SELECT id FROM show WHERE year_id = 2025)"""
        )
        cur.execute(
            "DELETE FROM show WHERE year_id = 2025 AND short_name IN ('sf1', 'sf2', 'sc', 'f')"
        )
        cur.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        cur.execute(
            """UPDATE country
               SET pot = NULL, genre = NULL, subgenre = NULL, semifinal_constraints = NULL
               WHERE id IN ('US', 'ES', 'FR', 'DE')"""
        )
    db.commit()


def test_draw_accepts_exactly_the_assignments_that_separate_each_pot(client, db, draw_setup):
    countries = ["US", "ES", "FR", "DE"]
    pots = {"US": 1, "ES": 1, "FR": 2, "DE": 2}

    @given(
        order=st.permutations(countries),
        constraint=st.sampled_from([1, 2, -1, -2]),
    )
    def property_test(order, constraint):
        db.execute("DELETE FROM song_show")
        db.execute(
            "UPDATE country SET semifinal_constraints = %s WHERE id = 'US'",
            ([constraint],),
        )
        db.commit()
        assignments = {"sf1": order[:2], "sf2": order[2:]}
        separates_pots = all(
            len({pots[country] for country in assigned}) == len(assigned)
            for assigned in assignments.values()
        )
        us_semifinal = next(
            number
            for number, assigned in enumerate(assignments.values(), start=1)
            if "US" in assigned
        )
        obeys_constraint = (
            us_semifinal == constraint if constraint > 0 else us_semifinal != -constraint
        )
        valid = separates_pots and obeys_constraint

        response = client.post(
            "/admin/manage/2025/draw",
            json={
                show: [draw_setup[country] for country in assigned]
                for show, assigned in assignments.items()
            },
        )

        assert response.status_code == (204 if valid else 400)
        count = db.execute("SELECT COUNT(*) AS n FROM song_show").fetchone()["n"]
        assert count == (len(countries) if valid else 0)

    property_test()


def test_published_individual_draw_uses_the_saved_running_order(client, db, draw_setup):
    countries = tuple(draw_setup)
    show_id = db.execute(
        "SELECT id FROM show WHERE year_id = 2025 AND short_name = 'sf1'"
    ).fetchone()["id"]

    @given(
        status=st.sampled_from(["draw", "partial", "full"]),
        order=st.permutations(countries),
    )
    def property_test(status, order):
        db.execute("DELETE FROM song_show WHERE show_id = %s", (show_id,))
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO song_show (song_id, show_id, running_order)
                   VALUES (%s, %s, %s)""",
                [
                    (draw_setup[country], show_id, position)
                    for position, country in enumerate(order, 1)
                ],
            )
        db.execute("UPDATE show SET status = %s WHERE id = %s", (status, show_id))
        db.commit()

        response = client.get(
            "/admin/manage/2025/draw/sf1", headers={"Accept": "application/json"}
        )

        assert response.status_code == 200
        assert [song["id"] for song in response.get_json()["draw_order"]] == [
            draw_setup[country] for country in order
        ]

    property_test()


def test_unpublished_draw_uses_host_then_source_show_qualification_order(
    client, db, draw_setup
):
    source_order = ["US", "ES", "FR", "DE"]
    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET host_id = 'US' WHERE id = 2025")
        final_id = cursor.execute(
            """INSERT INTO show (year_id, show_type, status)
               VALUES (2025, 'f', 'none') RETURNING id"""
        ).fetchone()["id"]
        repechage_id = cursor.execute(
            """INSERT INTO show (year_id, show_type, status)
               VALUES (2025, 'sc', 'none') RETURNING id"""
        ).fetchone()["id"]
        source_ids = {
            row["short_name"]: row["id"]
            for row in cursor.execute(
                """SELECT id, short_name FROM show
                   WHERE year_id = 2025 AND short_name IN ('sf1', 'sf2')"""
            ).fetchall()
        }
        cursor.execute(
            "DELETE FROM song_show WHERE show_id = ANY(%s)",
            (list(source_ids.values()),),
        )
        final_progression_sources = [
            source_ids["sf1"],
            source_ids["sf2"],
            repechage_id,
        ]
        cursor.executemany(
            """INSERT INTO show_progression (
                   source_show_id, target_show_id, qualifier_count, priority
               ) VALUES (%s, %s, 1, %s)""",
            [
                (source_ids["sf1"], final_id, 1),
                (source_ids["sf1"], repechage_id, 2),
                (source_ids["sf2"], final_id, 1),
                (source_ids["sf2"], repechage_id, 2),
                (repechage_id, final_id, 1),
            ],
        )
        cursor.executemany(
            """INSERT INTO song_show (song_id, show_id, running_order)
               VALUES (%s, %s, %s)""",
            [
                (draw_setup["US"], source_ids["sf1"], 1),
                (draw_setup["ES"], source_ids["sf1"], 2),
                (draw_setup["FR"], source_ids["sf2"], 1),
                (draw_setup["DE"], source_ids["sf2"], 2),
                (draw_setup["US"], repechage_id, 2),
                (draw_setup["DE"], repechage_id, 1),
            ],
        )
        cursor.executemany(
            """INSERT INTO song_show (song_id, show_id, running_order)
               VALUES (%s, %s, %s)""",
            [
                (draw_setup[country], final_id, position)
                for position, country in enumerate(source_order, 1)
            ],
        )
        cursor.executemany(
            """INSERT INTO show_qualifier (
                   source_show_id, target_show_id, song_id, qualifier_order
               ) VALUES (%s, %s, %s, 1)""",
            [
                (source_id, final_id, draw_setup[country])
                for source_id, country in zip(
                    final_progression_sources, source_order[1:], strict=True
                )
            ]
            + [
                (source_ids["sf1"], repechage_id, draw_setup["US"]),
                (source_ids["sf2"], repechage_id, draw_setup["DE"]),
            ],
        )
    db.commit()

    initial = client.get(
        "/admin/manage/2025/draw/f", headers={"Accept": "application/json"}
    ).get_json()
    expected_input = [draw_setup[country] for country in source_order]
    expected_draw = [song["id"] for song in initial["draw_order"]]
    assert [song["id"] for song in initial["songs"]] == expected_input
    initial_repechage = client.get(
        "/admin/manage/2025/draw/sc", headers={"Accept": "application/json"}
    ).get_json()
    expected_repechage_input = [draw_setup["US"], draw_setup["DE"]]
    expected_repechage_draw = [song["id"] for song in initial_repechage["draw_order"]]
    assert [song["id"] for song in initial_repechage["songs"]] == expected_repechage_input

    @given(
        target_order=st.permutations(source_order),
        repechage_order=st.permutations(("US", "DE")),
    )
    def property_test(target_order, repechage_order):
        with db.cursor() as cursor:
            cursor.executemany(
                """UPDATE song_show SET running_order = %s
                   WHERE show_id = %s AND song_id = %s""",
                [
                    (position, final_id, draw_setup[country])
                    for position, country in enumerate(target_order, 1)
                ],
            )
            cursor.executemany(
                """UPDATE song_show SET running_order = %s
                   WHERE show_id = %s AND song_id = %s""",
                [
                    (position, repechage_id, draw_setup[country])
                    for position, country in enumerate(repechage_order, 1)
                ],
            )
        db.commit()

        payload = client.get(
            "/admin/manage/2025/draw/f", headers={"Accept": "application/json"}
        ).get_json()

        assert [song["id"] for song in payload["songs"]] == expected_input
        assert [song["id"] for song in payload["draw_order"]] == expected_draw
        repechage_payload = client.get(
            "/admin/manage/2025/draw/sc", headers={"Accept": "application/json"}
        ).get_json()
        assert [song["id"] for song in repechage_payload["songs"]] == (
            expected_repechage_input
        )
        assert [song["id"] for song in repechage_payload["draw_order"]] == (
            expected_repechage_draw
        )

    property_test()


def test_set_pots_saves_and_clears_genres_and_subgenre(client, db, draw_setup):
    @given(
        tag=st.integers(min_value=0, max_value=32767),
        use_json=st.booleans(),
        genres=st.lists(st.integers(min_value=1, max_value=32767), max_size=6),
        semifinals=st.lists(st.sampled_from([-4, -3, -2, -1, 1, 2, 3, 4]), max_size=6),
    )
    def property_test(tag, use_json, genres, semifinals):
        db.execute("UPDATE country SET subgenre = 7 WHERE id IN ('ES', 'DE')")
        db.commit()
        if use_json:
            response = client.post(
                "/admin/manage/2025/setpots/json",
                json={"ES": {
                    "pot": 1, "genre": genres, "subgenre": tag, "semifinals": semifinals,
                }},
            )
        else:
            response = client.post(
                "/admin/manage/2025/setpots",
                data={
                    "pot_ES": 1, "genre_ES": ", ".join(map(str, genres)), "subgenre_ES": tag,
                    "semifinals_ES": ", ".join(map(str, semifinals)),
                },
            )
        assert response.status_code == 302
        saved = db.execute(
            "SELECT pot, genre, subgenre FROM country WHERE id = 'ES'"
        ).fetchone()
        assert saved["pot"] == 1
        assert set(saved["genre"] or []) == set(genres)
        assert saved["subgenre"] == (tag or None)
        assert db.execute(
            "SELECT subgenre FROM country WHERE id = 'DE'"
        ).fetchone()["subgenre"] is None
        payload = client.get(
            "/admin/manage/2025/setpots", headers={"Accept": "application/json"}
        ).get_json()
        country = next(row for row in payload["countries"] if row["id"] == "ES")
        assert country["subgenre"] == (tag or None)
        assert set(country["genre"] or []) == set(genres)
        assert set(country["semifinal_constraints"] or []) == set(semifinals)
        if use_json:
            response = client.post(
                "/admin/manage/2025/setpots/json",
                json={"ES": {"semifinals": ", ".join(map(str, semifinals))}},
            )
            assert response.status_code == 400
            unchanged = db.execute(
                "SELECT semifinal_constraints FROM country WHERE id = 'ES'"
            ).fetchone()
            assert set(unchanged["semifinal_constraints"] or []) == set(semifinals)

    property_test()
