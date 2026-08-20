"""Properties for published show-result endpoints."""

from hypothesis import given
from hypothesis import strategies as st


def _seed_results_show(db):
    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2024")
        cursor.execute(
            """INSERT INTO show_status (name)
               VALUES ('full'), ('partial'), ('draw') ON CONFLICT DO NOTHING"""
        )
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (20, 1) ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """INSERT INTO point (id, point_system_id, place, score)
               VALUES (201, 20, 1, 12), (202, 20, 2, 10)
               ON CONFLICT DO NOTHING"""
        )
        target_show_id = cursor.execute(
            """INSERT INTO show (year_id, point_system_id, show_type, status)
               VALUES (2024, 20, 'f', 'full')
               ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL
               DO UPDATE SET point_system_id = EXCLUDED.point_system_id
               RETURNING id"""
        ).fetchone()["id"]
        show_id = cursor.execute(
            """INSERT INTO show (
                   year_id, point_system_id, show_type, show_number,
                   voting_opens, voting_closes, status
               ) VALUES (
                   2024, 20, 'sf', 81,
                   CURRENT_TIMESTAMP - INTERVAL '2 hours',
                   CURRENT_TIMESTAMP - INTERVAL '1 hour', 'full'
               )
               ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL
               DO UPDATE SET point_system_id = EXCLUDED.point_system_id,
                             voting_opens = EXCLUDED.voting_opens,
                             voting_closes = EXCLUDED.voting_closes,
                             status = EXCLUDED.status
               RETURNING id"""
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO show_progression (
                   source_show_id, target_show_id, qualifier_count, priority
               ) VALUES (%s, %s, 1, 1)
               ON CONFLICT (source_show_id, target_show_id)
               DO UPDATE SET qualifier_count = 1""",
            (show_id, target_show_id),
        )
        song_ids = {}
        for country_id, title in (("US", "Winner"), ("ES", "Runner-up")):
            song_id = cursor.execute(
                """INSERT INTO song (country_id, year_id)
                   VALUES (%s, 2024) RETURNING id""",
                (country_id,),
            ).fetchone()["id"]
            song_ids[country_id] = song_id
            cursor.execute(
                """INSERT INTO song_data (song_id, title, artist_credit_set_id)
                   VALUES (%s, %s, test_artist_credit('Artist'))""",
                (song_id, title),
            )
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [
                (song_id, show_id, position)
                for position, song_id in enumerate(song_ids.values(), start=1)
            ],
        )
        vote_set_id = cursor.execute(
            """INSERT INTO vote_set (voter_id, show_id, nickname)
               VALUES (2, %s, 'Bob') RETURNING id""",
            (show_id,),
        ).fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, song_ids["US"], 12),
                (vote_set_id, song_ids["ES"], 10),
            ],
        )
    db.commit()
    return show_id, target_show_id, song_ids


def test_publication_state_controls_the_visible_result_contract(client, db):
    show_id, _target_show_id, song_ids = _seed_results_show(db)

    @given(
        show_status=st.sampled_from(["draw", "partial", "full"]),
        year_status=st.sampled_from(["open", "closed"]),
    )
    def property_test(show_status, year_status):
        db.execute("UPDATE year SET status = %s WHERE id = 2024", (year_status,))
        db.execute("UPDATE show SET status = %s WHERE id = %s", (show_status, show_id))
        db.commit()

        response = client.get("/api/results/2024-sf81")

        if year_status == "open":
            assert response.status_code == 403
            return
        assert response.status_code == 200
        data = response.get_json()["result"]
        assert data["access"] == show_status
        visible_ids = {entry["song_id"] for entry in data["entries"]}
        qualifier_ids = {entry["song_id"] for entry in data.get("qualifiers", [])}
        assert visible_ids.isdisjoint(qualifier_ids)
        if show_status == "draw":
            assert visible_ids == set(song_ids.values())
            assert all("total_points" not in entry for entry in data["entries"])
        elif show_status == "partial":
            assert visible_ids | qualifier_ids == set(song_ids.values())
            assert len(qualifier_ids) == 1
        else:
            assert visible_ids == set(song_ids.values())
            detailed = client.get("/api/results/2024-sf81/detailed")
            assert detailed.status_code == 200
            assert {
                vote["score"]
                for voter in detailed.get_json()["result"]["voters"]
                for vote in voter["votes"]
            } == {10, 12}

    property_test()


def test_special_qualifiers_move_entries_from_results_to_the_qualifier_set(client, db):
    show_id, target_show_id, song_ids = _seed_results_show(db)
    db.execute("UPDATE show SET status = 'partial' WHERE id = %s", (show_id,))
    db.commit()

    @given(include_special=st.booleans())
    def property_test(include_special):
        db.execute(
            """DELETE FROM show_qualifier
               WHERE source_show_id = %s AND song_id = %s""",
            (show_id, song_ids["ES"]),
        )
        db.execute(
            "DELETE FROM song_show WHERE show_id = %s AND song_id = %s",
            (target_show_id, song_ids["ES"]),
        )
        if include_special:
            db.execute(
                "INSERT INTO song_show (song_id, show_id) VALUES (%s, %s)",
                (song_ids["ES"], target_show_id),
            )
            db.execute(
                """INSERT INTO show_qualifier (
                       source_show_id, target_show_id, song_id,
                       qualifier_order, is_special
                   ) VALUES (%s, %s, %s, 2, true)""",
                (show_id, target_show_id, song_ids["ES"]),
            )
        db.execute("SELECT refresh_show_results_for_mode(%s, 'official')", (show_id,))
        db.commit()

        data = client.get("/api/results/2024-sf81").get_json()["result"]
        qualifiers = {entry["song_id"]: entry for entry in data["qualifiers"]}
        entries = {entry["song_id"] for entry in data["entries"]}
        assert (song_ids["ES"] in qualifiers) is include_special
        assert (song_ids["ES"] in entries) is not include_special
        if include_special:
            assert qualifiers[song_ids["ES"]]["special"] is True

    property_test()
