from hypothesis import given
from hypothesis import strategies as st


def test_final_only_year_exposes_its_final_results(client, db):
    @given(total_points=st.integers(0, 100))
    def property_test(total_points):
        with db.cursor() as cursor:
            cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
            cursor.execute(
                """INSERT INTO point_system (metadata) VALUES ('{"points": [12]}') RETURNING id"""
            )
            point_system_id = cursor.fetchone()["id"]
            cursor.execute(
                """INSERT INTO show (year_id, point_system_id, show_type, status)
                   VALUES (2024, %s, 'f', 'full') RETURNING id""",
                (point_system_id,),
            )
            show_id = cursor.fetchone()["id"]
            cursor.execute(
                """INSERT INTO song (
                       country_id, year_id, entry_number, main_participant
                   ) VALUES ('US', 2024, 1, true) RETURNING id"""
            )
            song_id = cursor.fetchone()["id"]
            cursor.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist_credit_set_id
                   ) VALUES (
                       %s, 3, 'Final-only entry', test_artist_credit('Final Artist')
                   )""",
                (song_id,),
            )
            cursor.execute(
                """INSERT INTO country_show_results (
                       country_id, country_name, show_id, short_name, year_id, song_id,
                       running_order, total_points, total_votes_received,
                       point_distribution, place, total_countries,
                       placement_percentage, max_possible_points, points_percentage,
                       adjusted_points_percentage, adjusted_max_possible_points,
                       points_midpoint, max_pts, total_voters
                   ) VALUES (
                       'US', 'United States', %s, 'f', 2024, %s,
                       1, %s, 1, '{}'::jsonb, 1, 1,
                       100, 100, %s,
                       %s, 100, 0, 12, 1
                   )""",
                (show_id, song_id, total_points, total_points, total_points),
            )
        db.commit()
        try:
            response = client.get("/year/2024", headers={"Accept": "application/json"})
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["has_f"] is True
            assert payload["has_sc"] is False
            assert payload["has_sf"] is False
            assert payload["results"][str(song_id)]["f"]["pts"] == total_points
            assert client.get("/year/2024").status_code == 200
        finally:
            db.execute("DELETE FROM country_show_results WHERE show_id = %s", (show_id,))
            db.execute("DELETE FROM song_data WHERE song_id = %s", (song_id,))
            db.execute("DELETE FROM song WHERE id = %s", (song_id,))
            db.execute("DELETE FROM show WHERE id = %s", (show_id,))
            db.execute("DELETE FROM point_system WHERE id = %s", (point_system_id,))
            db.commit()

    property_test()


def test_year_results_use_normalized_round_tiebreaks(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute(
            """
            INSERT INTO country (id, name, cc3)
            VALUES ('NZ', 'New Zealand', 'NZL'),
                   ('TH', 'Thailand', 'THA'),
                   ('DE', 'Germany', 'DEU'),
                   ('AT', 'Austria', 'AUT'),
                   ('BE', 'Belgium', 'BEL')
            ON CONFLICT DO NOTHING
            """
        )
        cursor.execute("INSERT INTO point_system DEFAULT VALUES RETURNING id")
        point_system_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO show (year_id, point_system_id, show_type, status)
            VALUES (2024, %s, 'f', 'full')
            RETURNING id
            """,
            (point_system_id,),
        )
        final_id = cursor.fetchone()["id"]

        semifinal_ids = {}
        # Create these out of numerical order so show_id cannot accidentally
        # stand in for show_number in the final deterministic fallback.
        for show_number in (2, 1):
            cursor.execute(
                """
                INSERT INTO show (
                    year_id, point_system_id, show_type, show_number, status
                )
                VALUES (2024, %s, 'sf', %s, 'full')
                RETURNING id
                """,
                (point_system_id, show_number),
            )
            semifinal_id = cursor.fetchone()["id"]
            semifinal_ids[show_number] = semifinal_id
            cursor.execute(
                """
                INSERT INTO show_progression (
                    source_show_id, target_show_id, qualifier_count, priority
                )
                VALUES (%s, %s, 1, 1)
                """,
                (semifinal_id, final_id),
            )

        entries = (
            # Higher point share outranks the better semifinal place.
            ("US", "United States", 1, 2, 30, 120, 3, 10, '{"12": 2, "6": 1}'),
            ("ES", "Spain", 2, 1, 24, 120, 2, 10, '{"12": 2}'),
            # At equal point share, voter share puts NZ above TH, matching 1981.
            ("NZ", "New Zealand", 1, 13, 18, 120, 6, 10, '{"3": 6}'),
            ("TH", "Thailand", 1, 14, 18, 120, 2, 10, '{"12": 1, "6": 1}'),
            # Equal point and voter shares: 2/10 twelves beats 3/20 twelves.
            ("FR", "France", 1, 5, 26, 120, 4, 10, '{"12": 2, "1": 2}'),
            (
                "DE",
                "Germany",
                2,
                5,
                52,
                240,
                8,
                20,
                '{"12": 3, "8": 1, "4": 1, "2": 1, "1": 2}',
            ),
            # Fully tied entries fall back to show number, not creation/show ID.
            ("AT", "Austria", 1, 8, 12, 120, 1, 10, '{"12": 1}'),
            ("BE", "Belgium", 2, 8, 12, 120, 1, 10, '{"12": 1}'),
        )
        song_ids = {}
        for (
            country_id,
            country_name,
            show_number,
            round_place,
            total_points,
            max_possible_points,
            total_votes_received,
            total_voters,
            point_distribution,
        ) in entries:
            cursor.execute(
                "INSERT INTO song (country_id, year_id) VALUES (%s, 2024) RETURNING id",
                (country_id,),
            )
            song_id = cursor.fetchone()["id"]
            song_ids[country_id] = song_id
            cursor.execute(
                """
                INSERT INTO country_show_results (
                    country_id, country_name, show_id, short_name, year_id, song_id,
                    running_order, total_points, total_votes_received,
                    point_distribution, place, total_countries,
                    placement_percentage, max_possible_points, points_percentage,
                    adjusted_points_percentage, adjusted_max_possible_points,
                    points_midpoint, max_pts, total_voters
                )
                VALUES (
                    %s, %s, %s, %s, 2024, %s,
                    1, %s, %s, %s, %s, 10,
                    50, %s, 0,
                    0, %s, 0, 12, %s
                )
                """,
                (
                    country_id,
                    country_name,
                    semifinal_ids[show_number],
                    f"sf{show_number}",
                    song_id,
                    total_points,
                    total_votes_received,
                    point_distribution,
                    round_place,
                    max_possible_points,
                    max_possible_points,
                    total_voters,
                ),
            )

        cursor.execute("SELECT refresh_year_results(2024)")
        cursor.execute("SELECT country_id, place FROM country_year_results WHERE year_id = 2024")
        places = {row["country_id"]: row["place"] for row in cursor.fetchall()}

    @given(ordered_pair=st.sampled_from([("US", "ES"), ("NZ", "TH"), ("FR", "DE"), ("AT", "BE")]))
    def property_test(ordered_pair):
        higher, lower = ordered_pair
        assert places[higher] < places[lower]

    property_test()
