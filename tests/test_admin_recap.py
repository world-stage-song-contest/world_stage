import json
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
                INSERT INTO song (country_id, year_id, submitter_id, artist, title)
                VALUES (%s, 2025, 1, %s, %s)
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
    assert "Opening act" not in response.text
    assert (
        ";https://media.world-stage.org/ws2025es.json\n"
        "[HOST] United States;https://media.world-stage.org/postcards/us.mov\n"
        "[HOST] Host Artist - Host Song;https://media.world-stage.org/ws2025us.json\n"
        ";https://media.world-stage.org/postcards/fr.mov"
    ) in response.text
    assert "Recap 2;https://media.world-stage.org/recaps/2025sf1s.mov" in response.text
    assert "https://media.world-stage.org/intervals/2025/sf1/i3.json" in response.text

    response = client.post(
        "/admin/recapdata",
        data={"type": "show", "show": "2025-sf1", "action": "download"},
    )

    assert response.status_code == 200
    recap_data = json.loads(response.text)
    assert all(row["year"] == 2025 for row in recap_data)
    assert all(row["submitter"] == "alice" for row in recap_data)
    assert all("short_name" not in row and "show_name" not in row for row in recap_data)


def test_cytube_playlist_adds_opening_act_by_prior_year_placement(client, db):
    session_id = str(uuid.uuid4())
    opening_act_shows = (
        ("f", 1, "US"),
        ("sc", 2, "ES"),
        ("sf4", 3, "FR"),
        ("sf3", 4, "DE"),
        ("sf2", 5, "IT"),
        ("sf1", 6, "PL"),
    )

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
            INSERT INTO country (id, name, is_participating, cc3)
            VALUES ('DE', 'Germany', true, 'DEU'),
                   ('IT', 'Italy', true, 'ITA'),
                   ('PL', 'Poland', true, 'POL')
            ON CONFLICT DO NOTHING
            """
        )
        cur.execute(
            """
            INSERT INTO year (id, status, host_id)
            VALUES (2026, 'open', 'US')
            ON CONFLICT DO NOTHING
            """
        )

        for short_name, placement, country in opening_act_shows:
            cur.execute(
                """
                INSERT INTO song (country_id, year_id, artist, title)
                VALUES (%s, 2025, 'Previous Artist', 'Previous Song')
                RETURNING id
                """,
                (country,),
            )
            song_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO country_year_results (
                    country_id, country_name, year_id, song_id,
                    place, total_countries, placement_percentage
                )
                SELECT id, name, 2025, %s, %s, 6, 100 - ((%s - 1) * 20)
                FROM country
                WHERE id = %s
                """,
                (song_id, placement, placement, country),
            )
            cur.execute(
                """
                INSERT INTO show (year_id, show_name, short_name)
                VALUES (2026, %s, %s)
                """,
                (f"Opening Act {short_name}", short_name),
            )
    db.commit()
    client.set_cookie("session", session_id)

    for short_name, _, country in opening_act_shows:
        response = client.post(
            "/admin/recapdata",
            data={"type": "show", "show": f"2026-{short_name}", "action": "cytube"},
            headers={"Accept": "text/html"},
        )

        assert response.status_code == 200
        assert (
            "WS 2026 Opening;https://media.world-stage.org/openings/2026.mov\n"
            f"Opening act;https://media.world-stage.org/ws2025{country.lower()}.json"
        ) in response.text


def test_all_recap_data_variants_include_submitter(client, db):
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
            INSERT INTO year (id, status, host_id)
            VALUES (2027, 'closed', 'US')
            """
        )
        cur.execute(
            """
            INSERT INTO show (year_id, show_name, short_name, date)
            VALUES (2027, 'Recap Export', 'f', '2027-05-01')
            RETURNING id
            """
        )
        show_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, submitter_id, artist, title)
            VALUES ('US', 2027, 1, 'Metadata Artist', 'Metadata Song')
            RETURNING id
            """
        )
        song_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, 1)",
            (song_id, show_id),
        )
    db.commit()
    client.set_cookie("session", session_id)

    for variant, selection in (
        ("show", "2027-f"),
        ("year", "2027"),
        ("country", "US"),
        ("submitter", "alice"),
    ):
        response = client.post(
            "/admin/recapdata",
            data={"type": variant, "show": selection, "action": "download"},
        )

        assert response.status_code == 200
        row = next(row for row in json.loads(response.text) if row["title"] == "Metadata Song")
        assert row["year"] == 2027
        assert row["submitter"] == "alice"
        assert "short_name" not in row
        assert "show_name" not in row
