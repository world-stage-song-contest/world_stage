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
        cur.execute("DELETE FROM show WHERE year_id = 2025 AND short_name IN ('sf1', 'sf2')")
        cur.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        cur.execute("UPDATE country SET pot = NULL WHERE id IN ('US', 'ES', 'FR', 'DE')")
    db.commit()


def test_draw_accepts_exactly_the_assignments_that_separate_each_pot(client, db, draw_setup):
    countries = ["US", "ES", "FR", "DE"]
    pots = {"US": 1, "ES": 1, "FR": 2, "DE": 2}

    @given(order=st.permutations(countries))
    def property_test(order):
        db.execute("DELETE FROM song_show")
        db.commit()
        assignments = {"sf1": order[:2], "sf2": order[2:]}
        valid = all(
            len({pots[country] for country in assigned}) == len(assigned)
            for assigned in assignments.values()
        )

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
