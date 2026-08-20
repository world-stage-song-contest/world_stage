import re
import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.routes.session import verify_password


def _clear_messaging_state(db):
    db.execute("DELETE FROM message")
    db.execute("DELETE FROM conversation_participant")
    db.execute("DELETE FROM conversation")
    db.execute("DELETE FROM session")
    db.commit()


def test_replies_notify_exactly_the_opted_in_other_participants(
    client, app, db, login, configured_email
):
    @settings(max_examples=20, deadline=None)
    @given(bob_opted_in=st.booleans(), carol_opted_in=st.booleans())
    def property_test(bob_opted_in, carol_opted_in):
        try:
            conversation_id = db.execute(
                "INSERT INTO conversation (subject) VALUES (%s) RETURNING id",
                (f"Property conversation {uuid.uuid4()}",),
            ).fetchone()["id"]
            with db.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO conversation_participant (
                           conversation_id, account_id, role, email_notifications
                       ) VALUES (%s, %s, %s, %s)""",
                    [
                        (conversation_id, 1, "owner", True),
                        (conversation_id, 2, "participant", bob_opted_in),
                        (conversation_id, 3, "participant", carol_opted_in),
                    ],
                )
            db.commit()
            login(1)
            app.extensions["mail_outbox"].clear()

            response = client.post(
                f"/messages/{conversation_id}/reply",
                data={"body": "Generated reply"},
            )

            assert response.status_code == 302
            expected = {
                email
                for email, opted_in in (
                    ("bob@test", bob_opted_in),
                    ("carol@test", carol_opted_in),
                )
                if opted_in
            }
            assert {message["To"] for message in app.extensions["mail_outbox"]} == expected
        finally:
            client.delete_cookie("session")
            _clear_messaging_state(db)

    property_test()


def test_password_reset_tokens_are_one_time_and_revoke_every_session(
    client, app, db, login, configured_email
):
    passwords = st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            whitelist_characters=" !@#$%^&*()-_=+",
        ),
        min_size=12,
        max_size=40,
    )

    @settings(max_examples=15, deadline=None)
    @given(password=passwords)
    def property_test(password):
        try:
            db.execute("UPDATE account SET email = 'bob@example.test' WHERE id = 2")
            session_ids = [login(2) for _ in range(2)]
            app.extensions["mail_outbox"].clear()

            requested = client.post(
                "/forgot-password",
                data={"email": "BOB@example.test"},
                headers={"Accept": "text/html"},
            )
            assert requested.status_code == 200
            assert len(app.extensions["mail_outbox"]) == 1
            body = app.extensions["mail_outbox"][0].get_content()
            reset_url = re.search(r"https://worldstage\.example/reset-password/\S+", body)
            assert reset_url is not None
            reset_path = reset_url.group(0).removeprefix("https://worldstage.example")

            reset = client.post(
                reset_path,
                data={"password": password, "password2": password},
            )
            assert reset.status_code == 200

            account = db.execute("SELECT password, salt FROM account WHERE id = 2").fetchone()
            assert verify_password(account["password"], account["salt"], password)
            assert (
                db.execute("SELECT count(*) AS count FROM session WHERE user_id = 2").fetchone()[
                    "count"
                ]
                == 0
            )
            assert client.get(reset_path).status_code == 400
            assert session_ids
        finally:
            client.delete_cookie("session")
            db.execute("DELETE FROM password_reset_token WHERE account_id = 2")
            db.execute("DELETE FROM session WHERE user_id = 2")
            db.commit()

    property_test()


def test_unknown_valid_addresses_never_receive_password_reset_mail(
    client, app, db, configured_email
):
    known_addresses = {
        row["email"].lower()
        for row in db.execute("SELECT email FROM account WHERE email IS NOT NULL").fetchall()
    }
    addresses = st.from_regex(
        r"[a-z0-9]{1,20}@[a-z]{1,12}\.[a-z]{2,6}",
        fullmatch=True,
    ).filter(lambda address: address.lower() not in known_addresses)

    @settings(max_examples=20, deadline=None)
    @given(address=addresses)
    def property_test(address):
        app.extensions["mail_outbox"].clear()

        response = client.post(
            "/forgot-password",
            data={"email": address},
            headers={"Accept": "text/html"},
        )

        assert response.status_code == 200
        assert app.extensions["mail_outbox"] == []

    property_test()
