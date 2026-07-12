import uuid


def test_cytube_playlist_inserts_and_labels_the_host(client, db):
    session_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (1, %s, CURRENT_TIMESTAMP + '1 day')
            """,
            (session_id,),
        )
        cur.execute(
            """
            INSERT INTO show (year_id, show_name, short_name)
            VALUES (2025, 'Playlist Semi-Final 1', 'sf1')
            RETURNING id
            """
        )
        show_id = cur.fetchone()["id"]

        song_ids = {}
        for country, artist, title in (
            ("ES", "Spanish Artist", "Spanish Song"),
            ("FR", "French Artist", "French Song"),
            ("US", "Host Artist", "Host Song"),
        ):
            cur.execute(
                """
                INSERT INTO song (country_id, year_id, artist, title)
                VALUES (%s, 2025, %s, %s)
                RETURNING id
                """,
                (country, artist, title),
            )
            song_ids[country] = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO song_show (song_id, show_id, running_order)
            VALUES (%s, %s, 1), (%s, %s, 2)
            """,
            (song_ids["ES"], show_id, song_ids["FR"], show_id),
        )
    db.commit()
    client.set_cookie("session", session_id)

    response = client.post(
        "/admin/recapdata",
        data={"type": "show", "show": "2025-sf1", "action": "cytube"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert "WS 2025 Opening;https://media.world-stage.org/openings/2025.mov" in response.text
    assert (
        ";https://media.world-stage.org/ws2025es.json\n"
        "[HOST] United States;https://media.world-stage.org/postcards/us.mov\n"
        "[HOST] Host Artist - Host Song;https://media.world-stage.org/ws2025us.json\n"
        ";https://media.world-stage.org/postcards/fr.mov"
    ) in response.text
    assert "Recap 2;https://media.world-stage.org/recaps/2025sf1s.mov" in response.text
    assert "https://media.world-stage.org/intervals/2025/sf1/i3.json" in response.text
