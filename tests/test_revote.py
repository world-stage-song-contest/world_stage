"""Regression coverage for permanent re-voting."""

from uuid import uuid4


def test_revote_keeps_official_results_unchanged(client, db, rendered_templates):
    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2024")
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (30, 1) ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """
            INSERT INTO point (id, point_system_id, place, score)
            VALUES (301, 30, 1, 12), (302, 30, 2, 10)
            ON CONFLICT DO NOTHING
            """
        )
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_type, show_number, status
            )
            VALUES (2024, 30, 'sf', 82, 'full')
            RETURNING id, revote_eligible_at
            """
        )
        show = cursor.fetchone()
        assert show["revote_eligible_at"] is not None
        show_id = show["id"]

        song_ids = []
        for country, title in (
            ("US", "Original winner"),
            ("ES", "Revote winner"),
        ):
            cursor.execute(
                """
                INSERT INTO song (country_id, year_id)
                VALUES (%s, 2024)
                RETURNING id
                """,
                (country,),
            )
            song_id = cursor.fetchone()["id"]
            song_ids.append(song_id)
            cursor.execute(
                """INSERT INTO song_data (song_id, title, artist_credit_set_id)
                   VALUES (%s, %s, test_artist_credit('Artist'))""",
                (song_id, title),
            )
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [(song_id, show_id, position) for position, song_id in enumerate(song_ids, start=1)],
        )

        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (2, %s, 'US', 'official') RETURNING id
            """,
            (show_id,),
        )
        official_set = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [(official_set, song_ids[0], 12), (official_set, song_ids[1], 10)],
        )
        db.commit()

        cursor.execute(
            """
            SELECT country_id, place, total_points
            FROM country_show_results
            WHERE show_id = %s AND result_mode = 'official'
            ORDER BY place
            """,
            (show_id,),
        )
        assert [(row["country_id"], row["total_points"]) for row in cursor.fetchall()] == [
            ("US", 12),
            ("ES", 10),
        ]

    response = client.get("/revote/2024/sf82", headers={"Accept": "text/html"})
    assert response.status_code == 200

    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (2, %s, 'US', 'revote') RETURNING id
            """,
            (show_id,),
        )
        revote_set = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [(revote_set, song_ids[0], 10), (revote_set, song_ids[1], 12)],
        )
        db.commit()

        cursor.execute(
            """
            SELECT country_id, place, total_points
            FROM country_show_results
            WHERE show_id = %s AND result_mode = 'official'
            ORDER BY place
            """,
            (show_id,),
        )
        assert [(row["country_id"], row["total_points"]) for row in cursor.fetchall()] == [
            ("US", 12),
            ("ES", 10),
        ]

        cursor.execute(
            """
            SELECT country_id, place, total_points
            FROM country_show_results
            WHERE show_id = %s AND result_mode = 'revote'
            ORDER BY place
            """,
            (show_id,),
        )
        assert [(row["country_id"], row["total_points"]) for row in cursor.fetchall()] == [
            ("ES", 12),
            ("US", 10),
        ]

    response = client.get("/revote/2024/sf82", headers={"Accept": "text/html"})
    assert response.status_code == 200

    response = client.get(
        "/revote/2024/sf82?revoters_only=true", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200

    response = client.get("/user/bob/revotes", headers={"Accept": "text/html"})
    assert response.status_code == 200

    response = client.get("/user/bob/revotes?view=year&year=2024", headers={"Accept": "text/html"})
    assert response.status_code == 200

    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (3, %s, 'ES', 'official') RETURNING id
            """,
            (show_id,),
        )
        original_set = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [(original_set, song_ids[0], 12), (original_set, song_ids[1], 10)],
        )
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (1, %s, 'FR', 'official') RETURNING id
            """,
            (show_id,),
        )
        alice_set = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, 10)",
            (alice_set, song_ids[0]),
        )
    db.commit()

    response = client.get(
        f"/revote/2024/sf82/song/{song_ids[0]}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200

    response = client.get(
        f"/revote/2024/sf82/song/{song_ids[1]}",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200

    response = client.get(
        "/revote/2024/sf82/detailed", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200

    response = client.get("/revote", headers={"Accept": "text/html"})
    assert response.status_code == 200

    response = client.get("/revote/2024", headers={"Accept": "text/html"})
    assert response.status_code == 200

    response = client.get("/year/2024/sf82", headers={"Accept": "text/html"})
    assert response.status_code == 200

    session_id = uuid4()
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (3, %s, CURRENT_TIMESTAMP + INTERVAL '1 hour')
            """,
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", str(session_id))
    response = client.post(
        "/revote/2024/sf82/vote",
        data={
            "nickname": "Carol",
            "country": "ES",
            "pts-12": str(song_ids[0]),
            "pts-10": str(song_ids[1]),
        },
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS count FROM vote_set
            WHERE voter_id = 3 AND show_id = %s AND result_mode = 'revote'
            """,
            (show_id,),
        )
        assert cursor.fetchone()["count"] == 1

    with db.cursor() as cursor:
        from world_stage.utils.song_revisions import create_song_revision

        create_song_revision(cursor, song_ids[0], {"submitter_id": 3}, changed_by=None)
    db.commit()

    response = client.get(
        "/revote/2024/sf82/vote", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200

    # Bob's revote now leaves the first song blank.  The song breakdown must
    # not fall back to the 12 points on his superseded official ballot.
    with db.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM vote
            WHERE song_id = %s AND vote_set_id = (
                SELECT id FROM vote_set
                WHERE voter_id = 2 AND show_id = %s AND result_mode = 'revote'
            )
            """,
            (song_ids[0], show_id),
        )
    db.commit()

    response = client.get(
        f"/revote/2024/sf82/song/{song_ids[0]}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    template_name, context = rendered_templates[-1]
    assert template_name == "year/song_votes.html"
    assert context["total_points"] == 22
    assert context["voters_who_gave"] == 2
    assert [voter["username"] for voter in context["no_points_voters"]] == ["bob"]
    assert context["no_points_voters"][0]["vote_change"] == -12
    point_voters = {
        voter["username"]: voter
        for group in context["point_groups"]
        for voter in group["voters"]
    }
    assert point_voters["carol"]["vote_change"] == 0
    assert point_voters["carol"]["revoted"] is True
    assert point_voters["alice"]["vote_change"] is None
    assert point_voters["alice"]["revoted"] is False
    assert "(-12)" in response.get_data(as_text=True)
    assert "(0)" in response.get_data(as_text=True)

    response = client.get(
        f"/revote/2024/sf82/song/{song_ids[1]}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    assert "(+2)" in response.get_data(as_text=True)

    # The breakdown template is shared with official results; revote-only
    # metadata must not be accessed when rendering an ordinary breakdown.
    response = client.get(
        "/year/2024/sf82/song/us", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
