"""Tests for the member submission form data endpoints."""

import uuid


def test_admin_can_edit_closed_song_without_submitter(client, db):
    """Unowned historical songs must still appear in an admin's edit list."""
    session_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO country (id, name, is_participating, cc3)
            VALUES ('HU', 'Hungary', true, 'HUN')
            """
        )
        cur.execute(
            """
            INSERT INTO year (id, status, host_id)
            VALUES (1970, 'closed', 'HU')
            """
        )
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, title, artist, is_placeholder)
            VALUES ('HU', 1970, 'Lost Record', 'Unknown', false)
            """
        )
        cur.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (1, %s, CURRENT_TIMESTAMP + '1 day')
            """,
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", session_id)

    response = client.get("/member/submit/1970")

    assert response.status_code == 200
    assert {country["cc"] for country in response.json["countries"]["placeholder"]} == {"HU"}
