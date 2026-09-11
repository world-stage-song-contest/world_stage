import string

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.messaging import has_unread_messages
from world_stage.utils import UserPermissions


def _conversation(
    db,
    *,
    owner: int | None,
    participants: list[int],
    admin_accessible: bool = False,
    system: bool = False,
) -> int:
    conversation_id = db.execute(
        """INSERT INTO conversation (
               subject, admin_accessible, created_by_admin, system_conversation
           ) VALUES ('Property conversation', %s, false, %s)
           RETURNING id""",
        (admin_accessible, system),
    ).fetchone()["id"]
    account_ids = list(dict.fromkeys(([owner] if owner is not None else []) + participants))
    with db.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO conversation_participant (
                   conversation_id, account_id, role
               ) VALUES (%s, %s, %s)""",
            [
                (
                    conversation_id,
                    account_id,
                    "owner" if account_id == owner else "participant",
                )
                for account_id in account_ids
            ],
        )
    db.commit()
    return conversation_id


def test_banners_clear_only_when_the_recipient_dismisses_or_successfully_replies(client, db, login):
    conversation_id = _conversation(db, owner=1, participants=[2, 3], admin_accessible=True)
    db.execute("UPDATE conversation SET created_by_admin = true WHERE id = %s", (conversation_id,))
    db.commit()

    @settings(max_examples=40, deadline=None)
    @given(
        recipient=st.sampled_from([1, 2, 3]),
        actor=st.sampled_from([None, 1, 2, 3]),
        action=st.sampled_from(["dismiss-banner", "reply", "invalid-reply"]),
    )
    def property_test(recipient, actor, action):
        db.execute(
            """UPDATE conversation
               SET metadata = jsonb_build_object('banner', true, 'submitter_id', %s::bigint)
               WHERE id = %s""",
            (recipient, conversation_id),
        )
        db.commit()
        client.delete_cookie("session")
        if actor is not None:
            login(actor)
        endpoint = "reply" if action == "invalid-reply" else action
        response = client.post(
            f"/messages/{conversation_id}/{endpoint}",
            data={"body": "" if action == "invalid-reply" else "Reply"},
        )
        assert response.status_code == (
            400 if action == "invalid-reply" and actor is not None else 302
        )
        login(recipient)
        home = client.get("/", headers={"Accept": "application/json"}).get_json()
        visible = {conversation["id"] for conversation in home["banner_conversations"]}
        cleared = actor == recipient and action != "invalid-reply"
        assert (conversation_id not in visible) == cleared
        assert client.get(f"/messages/{conversation_id}").status_code == 200

    property_test()


def test_message_pages_require_authentication(client):
    @given(
        path=st.sampled_from(["/messages", "/messages/new", "/messages/search", "/messages/1/edit"])
    )
    def property_test(path):
        response = client.get(path)

        assert response.status_code == 302
        assert response.location.endswith("/login")

    property_test()


def test_creating_a_conversation_persists_participation_and_first_message(client, db, login):
    safe_text = st.text(
        alphabet=string.ascii_letters + string.digits + " -_", min_size=1, max_size=40
    ).filter(lambda value: bool(value.strip()))

    @settings(max_examples=15)
    @given(
        creator=st.sampled_from([1, 2]),
        recipients=st.sampled_from([[3], [1, 3], [2, 3]]),
        requested_admin_access=st.booleans(),
        creator_notifications=st.booleans(),
        subject=safe_text,
        body=safe_text,
    )
    def property_test(
        creator,
        recipients,
        requested_admin_access,
        creator_notifications,
        subject,
        body,
    ):
        recipients = [account_id for account_id in recipients if account_id != creator]
        login(creator)
        before = db.execute("SELECT COALESCE(MAX(id), 0) AS id FROM conversation").fetchone()["id"]
        form = {
            "subject": subject,
            "participant_id": [str(account_id) for account_id in recipients],
            "body": body,
        }
        if requested_admin_access:
            form["admin_accessible"] = "on"
        if creator_notifications:
            form["email_notifications"] = "on"

        response = client.post("/messages/new", data=form)

        assert response.status_code == 302
        conversation = db.execute(
            """SELECT id, subject, admin_accessible, created_by_admin,
                      system_conversation
               FROM conversation WHERE id > %s ORDER BY id LIMIT 1""",
            (before,),
        ).fetchone()
        is_admin = creator == 1
        assert conversation == {
            "id": conversation["id"],
            "subject": subject.strip(),
            "admin_accessible": is_admin or requested_admin_access,
            "created_by_admin": is_admin,
            "system_conversation": False,
        }
        rows = db.execute(
            """SELECT account_id, role, email_notifications
               FROM conversation_participant
               WHERE conversation_id = %s ORDER BY account_id""",
            (conversation["id"],),
        ).fetchall()
        assert {row["account_id"] for row in rows} == {creator, *recipients}
        for row in rows:
            assert row["role"] == ("owner" if row["account_id"] == creator else "participant")
            expected_notifications = (
                creator_notifications if row["account_id"] == creator else is_admin
            )
            assert row["email_notifications"] is expected_notifications
        message = db.execute(
            """SELECT sender_id, sender_kind, body FROM message
               WHERE conversation_id = %s""",
            (conversation["id"],),
        ).fetchone()
        assert message == {
            "sender_id": creator,
            "sender_kind": "admin" if is_admin else "participant",
            "body": body,
        }

    property_test()


def test_participants_control_their_own_conversation_preferences(client, db, login):
    conversation_id = _conversation(db, owner=2, participants=[3])
    login(2)

    @given(email=st.booleans(), website=st.booleans(), pinned=st.booleans())
    def property_test(email, website, pinned):
        form = {}
        if email:
            form["email_notifications"] = "on"
        if website:
            form["website_notifications"] = "on"
        if pinned:
            form["pinned"] = "on"

        response = client.post(f"/messages/{conversation_id}/notifications", data=form)

        assert response.status_code == 302
        preferences = db.execute(
            """SELECT email_notifications, suppress_unread_highlight, pinned
               FROM conversation_participant
               WHERE conversation_id = %s AND account_id = 2""",
            (conversation_id,),
        ).fetchone()
        assert preferences == {
            "email_notifications": email,
            "suppress_unread_highlight": not website,
            "pinned": pinned,
        }

    property_test()


def test_website_notification_preference_controls_unread_attention(app, db):
    conversation_id = _conversation(db, owner=2, participants=[3])
    db.execute(
        """INSERT INTO message (conversation_id, sender_id, sender_kind, body)
           VALUES (%s, 3, 'participant', 'Unread message')""",
        (conversation_id,),
    )
    db.commit()

    with app.app_context():

        @given(website_notifications=st.booleans())
        def property_test(website_notifications):
            db.execute(
                """UPDATE conversation_participant
                   SET suppress_unread_highlight = %s
                   WHERE conversation_id = %s AND account_id = 2""",
                (not website_notifications, conversation_id),
            )
            db.commit()

            assert has_unread_messages(2, UserPermissions()) is website_notifications

        property_test()


def test_reply_access_follows_participation_and_shared_admin_access(client, db, login):
    conversation_id = _conversation(db, owner=2, participants=[3])

    @given(actor=st.sampled_from([1, 2, 3]), admin_accessible=st.booleans())
    def property_test(actor, admin_accessible):
        db.execute(
            "UPDATE conversation SET admin_accessible = %s WHERE id = %s",
            (admin_accessible, conversation_id),
        )
        db.execute(
            """DELETE FROM conversation_participant
               WHERE conversation_id = %s AND account_id = 1""",
            (conversation_id,),
        )
        db.commit()
        login(actor)
        before = db.execute(
            "SELECT COUNT(*) AS count FROM message WHERE conversation_id = %s",
            (conversation_id,),
        ).fetchone()["count"]

        response = client.post(
            f"/messages/{conversation_id}/reply", data={"body": f"Reply from {actor}"}
        )

        allowed = actor in {2, 3} or admin_accessible
        assert response.status_code == (302 if allowed else 404)
        messages = db.execute(
            """SELECT sender_id, sender_kind FROM message
               WHERE conversation_id = %s ORDER BY id""",
            (conversation_id,),
        ).fetchall()
        assert len(messages) == before + int(allowed)
        if allowed:
            assert messages[-1] == {
                "sender_id": actor,
                "sender_kind": "admin" if actor == 1 else "participant",
            }

    property_test()


def test_only_owner_edits_and_only_invited_participant_leaves(client, db, login):
    conversation_id = _conversation(db, owner=2, participants=[3])

    @given(actor=st.sampled_from([2, 3]))
    def property_test(actor):
        db.execute(
            """INSERT INTO conversation_participant (
                   conversation_id, account_id, role
               ) VALUES (%s, 3, 'participant')
               ON CONFLICT (conversation_id, account_id)
               DO UPDATE SET role = 'participant'""",
            (conversation_id,),
        )
        db.commit()
        login(actor)

        edit = client.get(f"/messages/{conversation_id}/edit")
        leave = client.post(f"/messages/{conversation_id}/leave")

        assert edit.status_code == (200 if actor == 2 else 403)
        assert leave.status_code == (403 if actor == 2 else 302)
        remains = db.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM conversation_participant
                   WHERE conversation_id = %s AND account_id = %s
               ) AS present""",
            (conversation_id, actor),
        ).fetchone()["present"]
        assert remains is (actor == 2)

    property_test()


def test_invalid_replies_never_create_messages(client, db, login):
    conversation_id = _conversation(db, owner=2, participants=[3])
    login(2)
    invalid_body = st.one_of(
        st.text(alphabet=" \t\n\r", min_size=0, max_size=30),
        st.text(alphabet=string.ascii_letters, min_size=5001, max_size=5100),
    )

    @settings(max_examples=10)
    @given(body=invalid_body)
    def property_test(body):
        before = db.execute(
            "SELECT COUNT(*) AS count FROM message WHERE conversation_id = %s",
            (conversation_id,),
        ).fetchone()["count"]

        response = client.post(f"/messages/{conversation_id}/reply", data={"body": body})

        assert response.status_code == 400
        after = db.execute(
            "SELECT COUNT(*) AS count FROM message WHERE conversation_id = %s",
            (conversation_id,),
        ).fetchone()["count"]
        assert after == before

    property_test()
