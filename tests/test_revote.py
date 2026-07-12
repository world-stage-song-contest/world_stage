"""Regression coverage for permanent re-voting."""

from uuid import uuid4


def test_revote_keeps_official_results_unchanged(client, db):
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
            INSERT INTO show (year_id, point_system_id, show_name, short_name, status, dtf)
            VALUES (2024, 30, 'Revote test', 'rv', 'full', 1)
            RETURNING id, revote_eligible_at
            """
        )
        show = cursor.fetchone()
        assert show["revote_eligible_at"] is not None
        show_id = show["id"]

        song_ids = []
        for country, title in (("US", "Original winner"), ("ES", "Revote winner")):
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
            ("US", 12), ("ES", 10)
        ]

    response = client.get("/revote/2024/rv", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"No revotes have been cast yet" in response.data
    assert b"Original winner" in response.data

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
            ("US", 12), ("ES", 10)
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
            ("ES", 12), ("US", 10)
        ]

    response = client.get("/revote/2024/rv", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"Revote results" in response.data
    assert b"<th>Place</th>" in response.data
    assert b"<th>Old</th>" in response.data
    assert response.data.count(b"<th>Diff</th>") == 2
    assert b">2024<" in response.data
    assert b"/year/2024/rv" in response.data
    assert b">Original Results<" in response.data
    assert b">Change Vote<" in response.data
    assert b"direct-to-final" in response.data
    assert b'class="first direct-to-final"' in response.data
    assert b'class="number old-result first direct-to-final"' in response.data
    assert f'/revote/2024/rv/song/{song_ids[0]}'.encode() in response.data

    response = client.get("/revote/2024/rv?revoters_only=true", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b'name="revoters_only"' in response.data
    assert b"Only use Revote votes" in response.data
    assert b"checked" in response.data

    response = client.get("/user/bob/revotes", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"Revote History: bob" in response.data
    assert b"user-votes revotes" in response.data
    assert b"points-difference" in response.data
    assert b">-2</td>" in response.data
    assert b">+2</td>" in response.data

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
    db.commit()

    response = client.get(f"/revote/2024/rv/song/{song_ids[0]}", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"Original winner" in response.data
    assert b">carol</a>" in response.data
    assert b"changed-vote" in response.data
    assert response.data.count(b'class="voter-entry changed-vote"') == 1

    response = client.get("/revote/2024/rv/detailed", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"revote-voter" in response.data
    assert response.data.count(b'title="bob"') == 1

    response = client.get("/revote", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"<h3>2024</h3>" in response.data
    assert b"Revote test" in response.data
    assert b">Vote<" in response.data
    assert b">Results<" in response.data

    response = client.get("/revote/2024", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"Revote test" in response.data

    response = client.get("/year/2024/rv", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"Revote Results" in response.data
    assert b"/revote/2024/rv" in response.data

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
        "/revote/2024/rv/vote",
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
        cursor.execute("UPDATE song SET submitter_id = 3 WHERE id = %s", (song_ids[0],))
    db.commit()

    response = client.get("/revote/2024/rv/vote", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert b"Carol" in response.data
    assert b"Original winner" not in response.data
    assert b"Revote winner" in response.data
    assert b">Clear<" in response.data
    assert b'onclick="clearVotes()"' in response.data
    assert b">Results Summary<" in response.data
    assert b">Detailed Results<" in response.data
