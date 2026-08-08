import re
import uuid

from world_stage.routes.session import verify_password


def _configure_email(app):
    app.config.update(
        MAIL_SERVER="smtp.test",
        MAIL_DEFAULT_SENDER="noreply@example.test",
        MAIL_SUPPRESS_SEND=True,
        SITE_URL="https://worldstage.example",
    )
    app.extensions["mail_outbox"].clear()


def _login(client, db, user_id: int):
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


def test_reply_emails_opted_in_participant(client, app, db):
    _configure_email(app)
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO conversation (subject)
            VALUES ('Email delivery test')
            RETURNING id
            """
        )
        conversation_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO conversation_participant (
                conversation_id, account_id, role, email_notifications
            ) VALUES (%s, 2, 'owner', true), (%s, 3, 'participant', false)
            """,
            (conversation_id, conversation_id),
        )
    db.commit()
    _login(client, db, 3)

    response = client.post(
        f"/messages/{conversation_id}/reply", data={"body": "Hello [b]Bob[/b]"}
    )

    assert response.status_code == 302
    assert len(app.extensions["mail_outbox"]) == 1
    email = app.extensions["mail_outbox"][0]
    assert email["To"] == "bob@test"
    assert email["Subject"] == "New message: Email delivery test"
    assert "Hello Bob" in email.get_content()
    assert f"https://worldstage.example/messages/{conversation_id}" in email.get_content()


def test_password_reset_is_expiring_one_time_and_revokes_sessions(client, app, db):
    _configure_email(app)
    with db.cursor() as cursor:
        cursor.execute("UPDATE account SET email = 'bob@example.test' WHERE id = 2")
    db.commit()
    _login(client, db, 2)

    response = client.post(
        "/forgot-password",
        data={"email": "bob@example.test"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert len(app.extensions["mail_outbox"]) == 1
    body = app.extensions["mail_outbox"][0].get_content()
    reset_url = re.search(r"https://worldstage\.example/reset-password/\S+", body)
    assert reset_url is not None
    reset_path = reset_url.group(0).removeprefix("https://worldstage.example")

    response = client.post(
        reset_path,
        data={"password": "new secure password", "password2": "new secure password"},
    )
    assert response.status_code == 200

    with db.cursor() as cursor:
        cursor.execute("SELECT password, salt FROM account WHERE id = 2")
        account = cursor.fetchone()
        assert verify_password(account["password"], account["salt"], "new secure password")
        cursor.execute("SELECT COUNT(*) AS count FROM session WHERE user_id = 2")
        assert cursor.fetchone()["count"] == 0

    reused = client.get(reset_path)
    assert reused.status_code == 400


def test_password_reset_does_not_reveal_unknown_account(client, app):
    _configure_email(app)

    response = client.post(
        "/forgot-password",
        data={"email": "unknown@example.test"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert b"If an approved account uses that email address" in response.data
    assert app.extensions["mail_outbox"] == []
