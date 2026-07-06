import uuid

import pytest


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
        cur.execute("""
            INSERT INTO show (year_id, show_name, short_name, status)
            VALUES (2025, 'Semi-Final 1', 'sf1', 'draw'),
                   (2025, 'Semi-Final 2', 'sf2', 'draw')
            ON CONFLICT (year_id, show_name) DO UPDATE
            SET short_name = EXCLUDED.short_name, status = EXCLUDED.status
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
                INSERT INTO song (
                    submitter_id, country_id, year_id, title, artist,
                    is_placeholder, admin_approved
                )
                VALUES (%s, %s, 2025, %s, 'Artist', false, true)
                RETURNING id
                """,
                (submitter, cc, f"{cc} Song"),
            )
            song_ids[cc] = cur.fetchone()["id"]

    db.commit()
    client.set_cookie("session", session_id)

    yield song_ids

    with db.cursor() as cur:
        cur.execute("DELETE FROM song_show")
        cur.execute("DELETE FROM show WHERE year_id = 2025 AND short_name IN ('sf1', 'sf2')")
        cur.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        cur.execute("UPDATE country SET pot = NULL WHERE id IN ('US', 'ES', 'FR', 'DE')")
    db.commit()


def test_draw_post_rejects_two_entries_from_same_pot_in_one_semifinal(client, draw_setup):
    res = client.post(
        "/admin/draw/2025",
        json={
            "sf1": [draw_setup["US"], draw_setup["ES"]],
            "sf2": [draw_setup["FR"], draw_setup["DE"]],
        },
    )

    assert res.status_code == 400
    assert res.json["error"] == "Show sf1 contains multiple entries from pot 1"


def test_draw_page_renders_backend_assigned_slots(client, draw_setup):
    res = client.get("/admin/draw/2025", headers={"Accept": "text/html,image/svg+xml"})

    assert res.status_code == 200
    html = res.text
    for song_id in draw_setup.values():
        assert f'data-id="{song_id}"' in html
    assert 'data-id=""' not in html


def test_draw_post_accepts_one_entry_per_pot_per_semifinal(client, db, draw_setup):
    res = client.post(
        "/admin/draw/2025",
        json={
            "sf1": [draw_setup["US"], draw_setup["FR"]],
            "sf2": [draw_setup["ES"], draw_setup["DE"]],
        },
    )

    assert res.status_code == 204

    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM song_show")
        assert cur.fetchone()["n"] == 4
