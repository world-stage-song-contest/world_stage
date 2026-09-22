from hypothesis import given, settings
from hypothesis import strategies as st


def test_partial_vote_history_requires_saved_qualifiers(client, db):
    db.execute(
        """INSERT INTO show_status (name)
           VALUES ('draw'), ('partial'), ('full') ON CONFLICT DO NOTHING"""
    )
    point_system_id = db.execute(
        """INSERT INTO point_system (metadata)
           VALUES ('{"points": [12, 10]}') RETURNING id"""
    ).fetchone()["id"]
    semifinal_id = db.execute(
        """INSERT INTO show (year_id, point_system_id, show_type, show_number, status)
           VALUES (2024, %s, 'sf', 1, 'partial') RETURNING id""",
        (point_system_id,),
    ).fetchone()["id"]
    final_id = db.execute(
        """INSERT INTO show (year_id, point_system_id, show_type, status)
           VALUES (2024, %s, 'f', 'draw') RETURNING id""",
        (point_system_id,),
    ).fetchone()["id"]
    db.execute(
        """INSERT INTO show_progression (
               source_show_id, target_show_id, qualifier_count, priority
           ) VALUES (%s, %s, 1, 1)""",
        (semifinal_id, final_id),
    )
    ballot_id = db.execute(
        "INSERT INTO vote_set (voter_id, show_id) VALUES (2, %s) RETURNING id",
        (semifinal_id,),
    ).fetchone()["id"]
    song_ids = []
    for country, score in (("US", 12), ("ES", 10)):
        song_id = db.execute(
            "INSERT INTO song (year_id, country_id) VALUES (2024, %s) RETURNING id",
            (country,),
        ).fetchone()["id"]
        song_ids.append(song_id)
        db.execute(
            """INSERT INTO song_data (song_id, title, artist_credit_set_id)
               VALUES (%s, 'Song', test_artist_credit('Artist'))""",
            (song_id,),
        )
        db.execute(
            "INSERT INTO song_show (song_id, show_id) VALUES (%s, %s)",
            (song_id, semifinal_id),
        )
        db.execute(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            (ballot_id, song_id, score),
        )
    db.commit()

    @settings(deadline=None)
    @given(
        status=st.sampled_from(["draw", "partial", "full"]),
        qualifier_id=st.one_of(st.none(), st.sampled_from(song_ids)),
        reveal=st.booleans(),
    )
    def property_test(status, qualifier_id, reveal):
        db.execute("UPDATE show SET status = %s WHERE id = %s", (status, semifinal_id))
        db.execute("DELETE FROM show_qualifier WHERE source_show_id = %s", (semifinal_id,))
        db.execute("DELETE FROM song_show WHERE show_id = %s", (final_id,))
        if qualifier_id is not None:
            db.execute(
                "INSERT INTO song_show (song_id, show_id) VALUES (%s, %s)",
                (qualifier_id, final_id),
            )
            db.execute(
                """INSERT INTO show_qualifier (
                       source_show_id, target_show_id, song_id, qualifier_order
                   ) VALUES (%s, %s, %s, 1)""",
                (semifinal_id, final_id, qualifier_id),
            )
        db.commit()

        response = client.get(
            "/user/bob/votes",
            query_string={"hidden": "false" if reveal else "true"},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 200
        history = response.get_json()
        visible = status == "full" or (status == "partial" and qualifier_id is not None)
        assert history["total_votes"] == int(visible)
        assert {ballot["id"] for ballot in history["votes"]} == (
            {ballot_id} if visible else set()
        )
        if visible:
            points = {entry["id"]: entry for entry in history["votes"][0]["points"]}
            assert set(points) == set(song_ids)
            for song_id, entry in points.items():
                hidden = status == "partial" and song_id == qualifier_id
                assert bool(entry["title"]) is not hidden
                if hidden:
                    assert entry["result_place"] is None

    property_test()
