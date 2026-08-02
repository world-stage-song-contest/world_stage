"""Tests for the member submission form data endpoints."""

import uuid


def test_placeholder_is_forced_after_two_regular_submissions(
    client, db, bob_headers
):
    body = {
        "year": 2025,
        "title": "Test Song",
        "artist": "Test Artist",
        "sources": "http://example.com",
        "languages": [20],
    }
    for country in ("US", "ES"):
        response = client.post(
            "/api/song", json={**body, "country": country}, headers=bob_headers
        )
        assert response.status_code == 201

    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO session (user_id, session_id, expires_at)
               VALUES (2, %s, CURRENT_TIMESTAMP + '1 day')""",
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", session_id)

    response = client.get("/member/submit/2025")

    assert response.status_code == 200
    assert response.json["countries"]["force_placeholder"] is True
    assert {country["cc"] for country in response.json["countries"]["placeholder"]} >= {
        "US",
        "ES",
        "FR",
    }


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
            WITH inserted AS (
                INSERT INTO song (country_id, year_id)
                VALUES ('HU', 1970) RETURNING id
            )
            INSERT INTO song_data (song_id, title, artist)
            SELECT id, 'Lost Record', 'Unknown' FROM inserted
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
