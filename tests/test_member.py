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
    assert response.json["countries"]["force_placeholder_reason"] == (
        "You already have 2 entries in this year, which is the maximum per user. "
        "Any additional submissions must be placeholders."
    )
    assert {country["cc"] for country in response.json["countries"]["placeholder"]} >= {
        "US",
        "ES",
        "FR",
    }


def test_placeholder_reason_explains_when_year_limit_is_reached(
    client, db, bob_headers, monkeypatch
):
    monkeypatch.setattr("world_stage.routes.member.MAX_YEAR_SUBMISSIONS", 2)
    for country in ("US", "ES"):
        response = client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": country,
                "title": "Test Song",
                "artist": "Test Artist",
                "sources": "http://example.com",
                "languages": [20],
            },
            headers=bob_headers,
        )
        assert response.status_code == 201

    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO session (user_id, session_id, expires_at)
               VALUES (3, %s, CURRENT_TIMESTAMP + '1 day')""",
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", session_id)

    response = client.get("/member/submit/2025")

    assert response.status_code == 200
    assert response.json["countries"]["force_placeholder"] is True
    assert response.json["countries"]["force_placeholder_reason"] == (
        "This year has reached its limit of 2 entries. "
        "Any additional submissions must be placeholders."
    )


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
            INSERT INTO song_data (song_id, title, artist_credit_set_id)
            SELECT id, 'Lost Record', test_artist_credit('Unknown') FROM inserted
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


def test_submit_form_preserves_year_and_country_url_params(client, db):
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO session (user_id, session_id, expires_at)
               VALUES (2, %s, CURRENT_TIMESTAMP + '1 day')""",
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", session_id)

    response = client.get(
        "/member/submit?year=2025&country=us", headers={"Accept": "text/html"}
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'const year = "2025";' in page
    assert 'const country = "us";' in page
    assert '<option value="2025" selected>2025</option>' in page
