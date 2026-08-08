import uuid


def _configure_email(app):
    app.config.update(
        MAIL_SERVER="smtp.test",
        MAIL_DEFAULT_SENDER="noreply@example.test",
        MAIL_SUPPRESS_SEND=True,
        SITE_URL="https://worldstage.example",
    )
    app.extensions["mail_outbox"].clear()


def _login(client, db, user_id=3):
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')
            """,
            (user_id, session_id),
        )
    db.commit()
    client.set_cookie("session", session_id)


def _create_song(client, headers, **overrides):
    body = {
        "year": 2025,
        "country": "US",
        "title": "Watched Song",
        "artist": "Watched Artist",
        "sources": "https://example.test/source",
        "languages": [20],
        **overrides,
    }
    response = client.post(
        "/api/song",
        headers=headers,
        json=body,
    )
    assert response.status_code == 201
    return response.get_json()["result"]["id"]


def _watch_us_spot(client, db, user_id=3):
    _login(client, db, user_id)
    response = client.post(
        "/year/2025/spot-watch",
        data={
            "country_id": "US",
            "entry_number": "1",
            "action": "watch",
        },
    )
    assert response.status_code == 302


def test_watching_spot_is_on_details_page_and_can_be_toggled(client, db, bob_headers):
    _create_song(client, bob_headers)
    _watch_us_spot(client, db)

    year_page = client.get("/year/2025", headers={"Accept": "text/html"})
    assert year_page.status_code == 200
    assert b"Watch spot" not in year_page.data

    details_page = client.get("/country/us/2025", headers={"Accept": "text/html"})
    assert details_page.status_code == 200
    assert b"Watching spot" in details_page.data

    response = client.post(
        "/year/2025/spot-watch",
        data={
            "country_id": "US",
            "entry_number": "1",
            "action": "unwatch",
        },
    )
    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM year_spot_watch")
        assert cursor.fetchone()["count"] == 0


def test_watch_button_and_new_watches_are_disabled_when_submissions_close(
    client, db, bob_headers
):
    _create_song(client, bob_headers)
    _login(client, db)
    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET submissions_open = false WHERE id = 2025")
    db.commit()

    try:
        page = client.get("/country/us/2025", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert b"Watch spot" not in page.data

        response = client.post(
            "/year/2025/spot-watch",
            data={
                "country_id": "US",
                "entry_number": "1",
                "action": "watch",
            },
        )
        assert response.status_code == 403
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET submissions_open = true WHERE id = 2025")
        db.commit()


def test_placeholder_change_creates_system_notification_and_email(
    client, app, db, bob_headers
):
    _configure_email(app)
    song_id = _create_song(client, bob_headers)
    _watch_us_spot(client, db)

    response = client.patch(
        f"/api/song/{song_id}",
        headers=bob_headers,
        json={"is_placeholder": True},
    )

    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT conversation.system_conversation, participant.account_id,
                   participant.email_notifications, message.sender_kind,
                   message.body
            FROM conversation
            JOIN conversation_participant AS participant
              ON participant.conversation_id = conversation.id
            JOIN message ON message.conversation_id = conversation.id
            WHERE conversation.metadata->>'spot_watch_event' = 'placeholder'
            """
        )
        notification = cursor.fetchone()
    assert notification == {
        "system_conversation": True,
        "account_id": 3,
        "email_notifications": True,
        "sender_kind": "system",
        "body": (
            "Watched Artist – Watched Song became a placeholder in the United "
            "States spot for 2025. You are receiving this because you watch this spot."
        ),
    }
    assert len(app.extensions["mail_outbox"]) == 1
    email = app.extensions["mail_outbox"][0]
    assert email["To"] == "carol@test"
    assert email["Subject"] == "Notification: Watched spot changed: United States in 2025"
    assert "became a placeholder" in email.get_content()


def test_deleting_song_creates_system_notification_and_email(
    client, app, db, bob_headers
):
    _configure_email(app)
    song_id = _create_song(client, bob_headers)
    _watch_us_spot(client, db)

    response = client.delete(f"/api/song/{song_id}", headers=bob_headers)

    assert response.status_code == 204
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT message.body
            FROM conversation
            JOIN message ON message.conversation_id = conversation.id
            WHERE conversation.metadata->>'spot_watch_event' = 'deleted'
            """
        )
        assert "was deleted" in cursor.fetchone()["body"]
        cursor.execute("SELECT COUNT(*) AS count FROM year_spot_watch")
        assert cursor.fetchone()["count"] == 1
    assert len(app.extensions["mail_outbox"]) == 1
    assert "was deleted" in app.extensions["mail_outbox"][0].get_content()


def test_submitting_placeholder_removes_submitters_watch(
    client, db, bob_headers, carol_headers
):
    song_id = _create_song(client, carol_headers)
    _watch_us_spot(client, db, user_id=2)
    assert client.delete(f"/api/song/{song_id}", headers=carol_headers).status_code == 204

    response = client.post(
        "/api/song",
        headers=bob_headers,
        json={
            "year": 2025,
            "country": "US",
            "title": "My Placeholder",
            "artist": "Placeholder Artist",
            "sources": "https://example.test/placeholder",
            "languages": [20],
            "is_placeholder": True,
        },
    )

    assert response.status_code == 201
    assert response.get_json()["result"]["is_placeholder"] is True
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM year_spot_watch
            WHERE account_id = 2 AND year_id = 2025 AND country_id = 'US'
            """
        )
        assert cursor.fetchone()["count"] == 0


def test_claiming_placeholder_removes_submitters_watch(
    client, db, bob_headers, carol_headers
):
    song_id = _create_song(client, carol_headers, is_placeholder=True)
    _watch_us_spot(client, db, user_id=2)

    response = client.put(
        f"/api/song/{song_id}",
        headers=bob_headers,
        json={
            "title": "Claimed Placeholder",
            "artist": "Placeholder Artist",
            "sources": "https://example.test/claimed-placeholder",
            "languages": [20],
            "is_placeholder": True,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["submitter_id"] == 2
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM year_spot_watch
            WHERE account_id = 2 AND year_id = 2025 AND country_id = 'US'
            """
        )
        assert cursor.fetchone()["count"] == 0
