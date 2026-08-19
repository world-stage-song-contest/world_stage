import datetime
import uuid


def _login(client, db, user_id: int):
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP + '1 day')
            """,
            (user_id, session_id),
        )
    db.commit()
    client.set_cookie("session", session_id)


def _context(rendered_templates, template_name: str):
    name, context = rendered_templates[-1]
    assert name == template_name
    return context


def _insert_conversation(
    db,
    *,
    owner_account_id: int | None,
    subject: str,
    participants: list[int],
    admin_accessible: bool = False,
    created_by_admin: bool = False,
    participant_roles: dict[int, str] | None = None,
) -> int:
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO conversation (
                subject, admin_accessible, created_by_admin, system_conversation
            )
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (
                subject,
                admin_accessible,
                created_by_admin,
                owner_account_id is None,
            ),
        )
        conversation_id = cursor.fetchone()["id"]
        participants = (
            list(dict.fromkeys([owner_account_id, *participants]))
            if owner_account_id
            else participants
        )
        participant_roles = participant_roles or {}
        cursor.executemany(
            """
            INSERT INTO conversation_participant (conversation_id, account_id, role)
            VALUES (%s, %s, %s)
            """,
            [
                (
                    conversation_id,
                    participant_id,
                    "owner"
                    if participant_id == owner_account_id
                    else participant_roles.get(participant_id, "participant"),
                )
                for participant_id in participants
            ],
        )
    db.commit()
    return conversation_id


def test_message_pages_require_login(client):
    for path in ("/messages", "/messages/new", "/messages/search", "/messages/1/edit"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.location.endswith("/login")


def test_user_can_create_group_conversation(client, db):
    _login(client, db, 2)

    response = client.post(
        "/messages/new",
        data={
            "subject": "Planning discussion",
            "participant_id": ["1", "3"],
            "admin_accessible": "on",
            "email_notifications": "on",
            "body": "First line\nSecond [b]line[/b]",
        },
    )

    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, subject, admin_accessible, created_by_admin,
                   system_conversation
            FROM conversation
            ORDER BY id DESC
            LIMIT 1
            """
        )
        conversation = cursor.fetchone()
        assert conversation["subject"] == "Planning discussion"
        assert conversation["admin_accessible"] is True
        assert conversation["created_by_admin"] is False
        assert conversation["system_conversation"] is False

        cursor.execute(
            """
            SELECT account_id, role, email_notifications
            FROM conversation_participant
            WHERE conversation_id = %s
            ORDER BY account_id
            """,
            (conversation["id"],),
        )
        assert cursor.fetchall() == [
            {"account_id": 1, "role": "participant", "email_notifications": False},
            {"account_id": 2, "role": "owner", "email_notifications": True},
            {"account_id": 3, "role": "participant", "email_notifications": False},
        ]

        cursor.execute(
            """
            SELECT sender_id, sender_kind, body
            FROM message
            WHERE conversation_id = %s
            """,
            (conversation["id"],),
        )
        assert cursor.fetchone() == {
            "sender_id": 2,
            "sender_kind": "participant",
            "body": "First line\nSecond [b]line[/b]",
        }


def test_admin_creation_is_recorded_and_includes_owner_as_participant(client, db):
    _login(client, db, 1)

    response = client.post(
        "/messages/new",
        data={
            "subject": "Administrator announcement",
            "participant_id": ["2", "3"],
            "body": "A message from the administration",
        },
    )

    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, admin_accessible, created_by_admin, system_conversation
            FROM conversation
            ORDER BY id DESC
            LIMIT 1
            """
        )
        conversation = cursor.fetchone()
        assert conversation["admin_accessible"] is True
        assert conversation["created_by_admin"] is True
        assert conversation["system_conversation"] is False

        cursor.execute(
            """
            SELECT account_id, role, email_notifications
            FROM conversation_participant
            WHERE conversation_id = %s
            ORDER BY account_id
            """,
            (conversation["id"],),
        )
        assert cursor.fetchall() == [
            {"account_id": 1, "role": "owner", "email_notifications": False},
            {"account_id": 2, "role": "participant", "email_notifications": True},
            {"account_id": 3, "role": "participant", "email_notifications": True},
        ]

        cursor.execute(
            """
            SELECT sender_id, sender_kind
            FROM message
            WHERE conversation_id = %s
            """,
            (conversation["id"],),
        )
        assert cursor.fetchone() == {"sender_id": 1, "sender_kind": "admin"}


def test_participant_can_change_own_email_notification_preference(
    client, db, rendered_templates
):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Notification preference",
        participants=[3],
    )
    _login(client, db, 2)

    response = client.post(
        f"/messages/{conversation_id}/notifications",
        data={
            "email_notifications": "on",
            "suppress_unread_highlight": "on",
            "pinned": "on",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith(f"/messages/{conversation_id}")

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT email_notifications, suppress_unread_highlight, pinned
            FROM conversation_participant
            WHERE conversation_id = %s AND account_id = 2
            """,
            (conversation_id,),
        )
        assert cursor.fetchone() == {
            "email_notifications": True,
            "suppress_unread_highlight": True,
            "pinned": True,
        }

        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 3, 'participant', 'Unread without the pink tint')
            """,
            (conversation_id,),
        )
    db.commit()

    newer_conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Newer unpinned conversation",
        participants=[3],
    )
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 3, 'participant', 'This conversation is newer')
            """,
            (newer_conversation_id,),
        )
    db.commit()

    inbox = client.get("/messages", headers={"Accept": "text/html"})
    assert inbox.status_code == 200
    conversations = _context(rendered_templates, "messages/inbox.html")["conversations"]
    assert [conversation["id"] for conversation in conversations[:2]] == [
        conversation_id,
        newer_conversation_id,
    ]
    assert conversations[0]["unread_count"] == 1
    assert conversations[0]["pinned"] is True

def test_unapproved_accounts_are_excluded_from_participant_selectors(
    client, db, rendered_templates
):
    with db.cursor() as cursor:
        cursor.execute("UPDATE account SET approved = false WHERE id = 3")
    db.commit()

    try:
        conversation_id = _insert_conversation(
            db,
            owner_account_id=2,
            subject="Existing conversation",
            participants=[2, 3],
        )
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO message (conversation_id, sender_id, sender_kind, body)
                VALUES (%s, 3, 'participant', 'Historical message')
                """,
                (conversation_id,),
            )
        db.commit()
        _login(client, db, 2)

        compose = client.get("/messages/new", headers={"Accept": "text/html"})
        assert compose.status_code == 200
        compose_context = _context(rendered_templates, "messages/new.html")
        assert 3 not in {recipient["id"] for recipient in compose_context["recipients"]}

        search = client.get("/messages/search", headers={"Accept": "text/html"})
        assert search.status_code == 200
        search_context = _context(rendered_templates, "messages/search.html")
        assert 3 not in {
            account["id"] for account in search_context["participant_filter_accounts"]
        }
        assert 3 in {
            account["id"] for account in search_context["sender_filter_accounts"]
        }
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE account SET approved = true WHERE id = 3")
        db.commit()


def test_search_accounts_are_scoped_to_interactions(client, app, db, rendered_templates):
    _insert_conversation(
        db,
        owner_account_id=2,
        subject="Shared group",
        participants=[2, 3],
    )
    admin_visible_conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Visible to admins",
        participants=[2, 3],
        admin_accessible=True,
    )
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 2, 'participant', 'Message visible to admins')
            """,
            (admin_visible_conversation_id,),
        )
    db.commit()

    _login(client, db, 2)
    user_search = client.get(
        "/messages/search",
        headers={"Accept": "text/html"},
    )
    assert user_search.status_code == 200
    user_context = _context(rendered_templates, "messages/search.html")
    assert {account["id"] for account in user_context["sender_filter_accounts"]} == {3}
    assert {account["id"] for account in user_context["participant_filter_accounts"]} == {
        3
    }

    admin_client = app.test_client()
    _login(admin_client, db, 1)
    admin_search = admin_client.get(
        "/messages/search",
        headers={"Accept": "text/html"},
    )
    assert admin_search.status_code == 200
    admin_context = _context(rendered_templates, "messages/search.html")
    assert {account["id"] for account in admin_context["sender_filter_accounts"]} == {2}
    assert {account["id"] for account in admin_context["participant_filter_accounts"]} == {
        2
    }


def test_admin_messaging_interface_shows_only_shared_conversations(
    client, app, db, rendered_templates
):
    private_conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Private user conversation",
        participants=[3],
    )
    shared_conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Shared with administrators",
        participants=[3],
        admin_accessible=True,
    )
    with db.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 2, 'participant', %s)
            """,
            [
                (private_conversation_id, "Private message"),
                (shared_conversation_id, "Shared message"),
            ],
        )
    db.commit()

    _login(client, db, 2)
    response = client.get("/admin/messages")
    assert response.status_code == 302
    assert response.location.endswith("/")

    admin_client = app.test_client()
    _login(admin_client, db, 1)
    response = admin_client.get("/admin/messages", headers={"Accept": "text/html"})
    assert response.status_code == 200
    conversations = _context(rendered_templates, "admin/messages.html")["conversations"]
    assert [conversation["id"] for conversation in conversations] == [
        shared_conversation_id
    ]
    assert conversations[0]["unread_count"] == 1

    compose = admin_client.get("/messages/new", headers={"Accept": "text/html"})
    assert compose.status_code == 200


def test_editor_can_use_moderator_inbox_and_reply_as_moderator(client, db):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Shared with moderators",
        participants=[2],
        admin_accessible=True,
    )
    with db.cursor() as cursor:
        cursor.execute("UPDATE account SET role = 'editor' WHERE id = 3")
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 2, 'participant', 'Please review this')
            """,
            (conversation_id,),
        )
    db.commit()
    _login(client, db, 3)

    home = client.get("/", headers={"Accept": "text/html"})
    inbox = client.get("/admin/messages", headers={"Accept": "text/html"})
    thread = client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
    reply = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "Reviewed by an editor"},
    )

    assert home.status_code == 200
    assert 'href="/admin/messages"' in home.text
    assert inbox.status_code == 200
    assert thread.status_code == 200
    assert reply.status_code == 302
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT message.sender_kind, participant.role
            FROM message
            JOIN conversation_participant AS participant
              ON participant.conversation_id = message.conversation_id
             AND participant.account_id = message.sender_id
            WHERE message.conversation_id = %s
              AND message.sender_id = 3
            """,
            (conversation_id,),
        )
        assert cursor.fetchone() == {"sender_kind": "admin", "role": "admin"}


def test_search_can_filter_messages_sent_by_current_user(client, db, rendered_templates):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Sender filter",
        participants=[2, 3],
    )
    with db.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, %s, 'participant', %s)
            """,
            [
                (conversation_id, 2, "Message sent by current user"),
                (conversation_id, 3, "Message sent by another user"),
            ],
        )
    db.commit()
    _login(client, db, 2)

    response = client.get(
        "/messages/search",
        query_string={"sender": "me"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    results = _context(rendered_templates, "messages/search.html")["results"]
    assert [result["sender_id"] for result in results] == [2]


def test_search_can_filter_system_messages(client, db, rendered_templates):
    system_conversation_id = _insert_conversation(
        db,
        owner_account_id=None,
        subject="Automated notice",
        participants=[2],
    )
    user_conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="User discussion",
        participants=[3],
    )
    with db.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (system_conversation_id, None, "system", "Automated alert"),
                (user_conversation_id, 3, "participant", "Human message"),
            ],
        )
    db.commit()
    _login(client, db, 2)

    response = client.get(
        "/messages/search",
        query_string={"sender": "system"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    results = _context(rendered_templates, "messages/search.html")["results"]
    assert len(results) == 1
    assert results[0]["sender_kind"] == "system"


def test_participant_and_shared_admin_access(client, app, db, rendered_templates):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Private first",
        participants=[2, 3],
    )
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 2, 'participant', 'Hello Carol')
            """,
            (conversation_id,),
        )
    db.commit()

    _login(client, db, 3)
    response = client.get(f"/messages/{conversation_id}", headers={"Accept": "text/html"})
    assert response.status_code == 200
    context = _context(rendered_templates, "messages/thread.html")
    assert [message["body"] for message in context["messages"]] == ["Hello Carol"]
    assert {participant["id"] for participant in context["participants"]} == {2, 3}

    response = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "Hello Bob"},
    )
    assert response.status_code == 302

    admin_client = app.test_client()
    _login(admin_client, db, 1)
    response = admin_client.get(f"/messages/{conversation_id}", headers={"Accept": "text/html"})
    assert response.status_code == 404

    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE conversation SET admin_accessible = true WHERE id = %s",
            (conversation_id,),
        )
    db.commit()

    response = admin_client.get(f"/messages/{conversation_id}", headers={"Accept": "text/html"})
    assert response.status_code == 200
    response = admin_client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "Administrator response"},
    )
    assert response.status_code == 302

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT sender_id, sender_kind
            FROM message
            WHERE body = 'Administrator response'
            """
        )
        assert cursor.fetchone() == {"sender_id": 1, "sender_kind": "admin"}
        cursor.execute(
            """
            SELECT role, email_notifications
            FROM conversation_participant
            WHERE conversation_id = %s AND account_id = 1
            """,
            (conversation_id,),
        )
        assert cursor.fetchone() == {"role": "admin", "email_notifications": False}

    response = admin_client.post(
        f"/messages/{conversation_id}/notifications",
        data={"email_notifications": "on"},
    )
    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT email_notifications
            FROM conversation_participant
            WHERE conversation_id = %s AND account_id = 1
            """,
            (conversation_id,),
        )
        assert cursor.fetchone()["email_notifications"] is True


def test_unread_counts_are_per_user_and_threads_mark_messages_read(
    client, app, db, rendered_templates
):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Unread tracking",
        participants=[2, 3],
    )
    with db.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, %s, 'participant', %s)
            """,
            [
                (conversation_id, 2, "Bob's own message"),
                (conversation_id, 3, "First unread message"),
                (conversation_id, 3, "Second unread message"),
            ],
        )
    db.commit()
    _login(client, db, 2)

    inbox = client.get("/messages", headers={"Accept": "text/html"})
    assert inbox.status_code == 200
    conversations = _context(rendered_templates, "messages/inbox.html")["conversations"]
    assert conversations[0]["unread_count"] == 2
    assert conversations[0]["latest_message_id"] is not None

    thread = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert thread.status_code == 200
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT last_read_message_id
            FROM conversation_read_state
            WHERE conversation_id = %s AND account_id = 2
            """,
            (conversation_id,),
        )
        last_read = cursor.fetchone()["last_read_message_id"]
        cursor.execute(
            "SELECT MAX(id) AS latest_message_id FROM message WHERE conversation_id = %s",
            (conversation_id,),
        )
        assert last_read == cursor.fetchone()["latest_message_id"]

    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 3, 'participant', 'A newly unread message')
            """,
            (conversation_id,),
        )
    db.commit()

    inbox = client.get("/messages", headers={"Accept": "text/html"})
    assert _context(rendered_templates, "messages/inbox.html")["conversations"][0][
        "unread_count"
    ] == 1

    carol_client = app.test_client()
    _login(carol_client, db, 3)
    carol_inbox = carol_client.get("/messages", headers={"Accept": "text/html"})
    assert carol_inbox.status_code == 200
    assert _context(rendered_templates, "messages/inbox.html")["conversations"][0][
        "unread_count"
    ] == 1


def test_shared_admin_unread_state_is_independent_for_each_admin(
    client, app, db, rendered_templates
):
    with db.cursor() as cursor:
        cursor.execute("UPDATE account SET role = 'admin' WHERE id = 3")
    db.commit()

    try:
        conversation_id = _insert_conversation(
            db,
            owner_account_id=2,
            subject="Admin inbox unread",
            participants=[2],
            admin_accessible=True,
        )
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO message (conversation_id, sender_id, sender_kind, body)
                VALUES (%s, 2, 'participant', 'Unread for every administrator')
                """,
                (conversation_id,),
            )
        db.commit()

        _login(client, db, 1)
        first_admin_inbox = client.get(
            "/admin/messages", headers={"Accept": "text/html"}
        )
        assert first_admin_inbox.status_code == 200
        assert _context(rendered_templates, "admin/messages.html")["conversations"][0][
            "unread_count"
        ] == 1
        client.get(
            f"/messages/{conversation_id}",
            headers={"Accept": "text/html"},
        )

        second_admin = app.test_client()
        _login(second_admin, db, 3)
        second_admin_inbox = second_admin.get(
            "/admin/messages",
            headers={"Accept": "text/html"},
        )
        assert second_admin_inbox.status_code == 200
        assert _context(rendered_templates, "admin/messages.html")["conversations"][0][
            "unread_count"
        ] == 1

    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE account SET role = 'user' WHERE id = 3")
        db.commit()


def test_moderator_personal_inbox_only_shows_conversations_they_participate_in(
    app, db, rendered_templates
):
    with db.cursor() as cursor:
        cursor.execute("UPDATE account SET role = 'admin' WHERE id = 3")
    db.commit()

    try:
        unrelated_conversation_id = _insert_conversation(
            db,
            owner_account_id=1,
            subject="Another moderator's message",
            participants=[2],
            admin_accessible=True,
            created_by_admin=True,
        )
        participating_conversation_id = _insert_conversation(
            db,
            owner_account_id=1,
            subject="Message including this moderator",
            participants=[2, 3],
            admin_accessible=True,
            created_by_admin=True,
        )
        with db.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO message (conversation_id, sender_id, sender_kind, body)
                VALUES (%s, 1, 'admin', %s)
                """,
                [
                    (unrelated_conversation_id, "Not for this moderator"),
                    (participating_conversation_id, "This moderator is included"),
                ],
            )
        db.commit()

        moderator_client = app.test_client()
        _login(moderator_client, db, 3)
        response = moderator_client.get(
            "/messages",
            headers={"Accept": "text/html"},
        )

        assert response.status_code == 200
        conversations = _context(
            rendered_templates, "messages/inbox.html"
        )["conversations"]
        assert [conversation["id"] for conversation in conversations] == [
            participating_conversation_id
        ]
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE account SET role = 'user' WHERE id = 3")
        db.commit()


def test_owner_can_edit_subject_participants_and_admin_access(
    client, db, rendered_templates
):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Original subject",
        participants=[2, 3],
    )
    _login(client, db, 2)

    edit_page = client.get(
        f"/messages/{conversation_id}/edit",
        headers={"Accept": "text/html"},
    )
    assert edit_page.status_code == 200
    edit_context = _context(rendered_templates, "messages/edit.html")
    assert edit_context["values"]["participant_ids"] == ["3"]
    assert {recipient["id"] for recipient in edit_context["recipients"]} == {1, 3}

    response = client.post(
        f"/messages/{conversation_id}/edit",
        data={
            "subject": "Updated subject",
            "participant_id": ["1"],
            "admin_accessible": "on",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith(f"/messages/{conversation_id}")

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT subject, admin_accessible, created_by_admin
            FROM conversation
            WHERE id = %s
            """,
            (conversation_id,),
        )
        assert cursor.fetchone() == {
            "subject": "Updated subject",
            "admin_accessible": True,
            "created_by_admin": False,
        }
        cursor.execute(
            """
            SELECT account_id
            FROM conversation_participant
            WHERE conversation_id = %s
            ORDER BY account_id
            """,
            (conversation_id,),
        )
        assert cursor.fetchall() == [{"account_id": 1}, {"account_id": 2}]

    thread = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert thread.status_code == 200
    thread_context = _context(rendered_templates, "messages/thread.html")
    assert thread_context["conversation"]["subject"] == "Updated subject"
    assert thread_context["can_edit"] is True


def test_non_owner_cannot_edit_user_conversation(client, db, rendered_templates):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Creator only",
        participants=[2, 3],
    )
    _login(client, db, 3)

    thread = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert thread.status_code == 200
    assert _context(rendered_templates, "messages/thread.html")["can_edit"] is False

    edit_page = client.get(
        f"/messages/{conversation_id}/edit",
        headers={"Accept": "text/html"},
    )
    assert edit_page.status_code == 403

    response = client.post(
        f"/messages/{conversation_id}/edit",
        data={"subject": "Unauthorized change", "participant_id": ["2"]},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 403
    with db.cursor() as cursor:
        cursor.execute("SELECT subject FROM conversation WHERE id = %s", (conversation_id,))
        assert cursor.fetchone()["subject"] == "Creator only"


def test_invited_participant_can_leave_but_owner_cannot(
    client, db, rendered_templates
):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Optional membership",
        participants=[3],
    )

    _login(client, db, 3)
    thread = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert thread.status_code == 200
    assert _context(rendered_templates, "messages/thread.html")["can_leave"] is True

    response = client.post(f"/messages/{conversation_id}/leave")
    assert response.status_code == 302
    assert response.location.endswith("/messages")

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT account_id, role
            FROM conversation_participant
            WHERE conversation_id = %s
            ORDER BY account_id
            """,
            (conversation_id,),
        )
        assert cursor.fetchall() == [{"account_id": 2, "role": "owner"}]

    assert client.get(f"/messages/{conversation_id}").status_code == 404

    _login(client, db, 2)
    owner_thread = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert owner_thread.status_code == 200
    assert _context(rendered_templates, "messages/thread.html")["can_leave"] is False
    assert client.post(f"/messages/{conversation_id}/leave").status_code == 403


def test_disabling_admin_access_keeps_admin_as_an_ordinary_participant(client, app, db):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Admin capacity",
        participants=[1, 3],
        admin_accessible=True,
        participant_roles={1: "admin"},
    )
    _login(client, db, 2)

    response = client.post(
        f"/messages/{conversation_id}/edit",
        data={
            "subject": "Admin capacity",
            "participant_id": ["1", "3"],
        },
    )
    assert response.status_code == 302

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT conversation.admin_accessible, conversation_participant.role
            FROM conversation
            JOIN conversation_participant
              ON conversation_participant.conversation_id = conversation.id
            WHERE conversation.id = %s
              AND conversation_participant.account_id = 1
            """,
            (conversation_id,),
        )
        assert cursor.fetchone() == {
            "admin_accessible": False,
            "role": "participant",
        }

    admin_client = app.test_client()
    _login(admin_client, db, 1)
    response = admin_client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "An unofficial reply"},
    )
    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT sender_kind
            FROM message
            WHERE conversation_id = %s AND body = 'An unofficial reply'
            """,
            (conversation_id,),
        )
        assert cursor.fetchone()["sender_kind"] == "participant"


def test_all_admins_can_edit_admin_created_conversation(client, db):
    with db.cursor() as cursor:
        cursor.execute("UPDATE account SET role = 'admin' WHERE id = 3")
    db.commit()

    try:
        conversation_id = _insert_conversation(
            db,
            owner_account_id=1,
            subject="Shared admin ownership",
            participants=[2],
            admin_accessible=True,
            created_by_admin=True,
        )
        _login(client, db, 3)

        edit_page = client.get(
            f"/messages/{conversation_id}/edit",
            headers={"Accept": "text/html"},
        )
        assert edit_page.status_code == 200

        response = client.post(
            f"/messages/{conversation_id}/edit",
            data={
                "subject": "Edited by another admin",
                "participant_id": ["2", "3"],
            },
        )
        assert response.status_code == 302

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT subject, admin_accessible, created_by_admin
                FROM conversation
                WHERE id = %s
                """,
                (conversation_id,),
            )
            assert cursor.fetchone() == {
                "subject": "Edited by another admin",
                "admin_accessible": True,
                "created_by_admin": True,
            }
            cursor.execute(
                """
                SELECT account_id, role
                FROM conversation_participant
                WHERE conversation_id = %s
                ORDER BY account_id
                """,
                (conversation_id,),
            )
            assert cursor.fetchall() == [
                {"account_id": 1, "role": "owner"},
                {"account_id": 2, "role": "participant"},
                {"account_id": 3, "role": "participant"},
            ]
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE account SET role = 'user' WHERE id = 3")
        db.commit()


def test_user_can_hide_message_avatars(client, db, rendered_templates):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Avatar preference",
        participants=[2, 3],
    )
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, 3, 'participant', 'Avatar is optional')
            """,
            (conversation_id,),
        )
    db.commit()
    _login(client, db, 2)

    settings = client.post(
        "/settings",
        data={"theme": "auto", "hide_message_avatars": "true"},
        headers={"Accept": "text/html"},
    )
    assert settings.status_code == 200

    response = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 200
    assert _context(rendered_templates, "messages/thread.html")["show_avatars"] is False

    client.post(
        "/settings",
        data={"theme": "auto"},
        headers={"Accept": "text/html"},
    )
    response = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 200
    assert _context(rendered_templates, "messages/thread.html")["show_avatars"] is True


def test_system_conversation_cannot_be_replied_to(client, db, rendered_templates):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=None,
        subject="System maintenance",
        participants=[2],
    )
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, NULL, 'system', %s)
            """,
            (conversation_id, "System notice"),
        )
    db.commit()
    _login(client, db, 2)

    response = client.get(f"/messages/{conversation_id}", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert _context(rendered_templates, "messages/thread.html")["can_reply"] is False

    response = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "Attempted reply"},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 403


def test_search_filters_body_sender_participant_subject_and_exact_date(
    client, db, rendered_templates
):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Specific planning topic",
        participants=[2, 3],
    )
    sent_at = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.UTC)
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body, created_at)
            VALUES (%s, 3, 'participant', '[b]searchable needle[/b]', %s)
            """,
            (conversation_id, sent_at),
        )
    db.commit()
    _login(client, db, 2)

    response = client.get(
        "/messages/search",
        query_string={
            "q": "searchable needle",
            "subject": "planning",
            "sender": "3",
            "participant": "3",
            "date_mode": "exact",
            "date": "2026-07-20",
        },
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    context = _context(rendered_templates, "messages/search.html")
    assert [result["conversation_id"] for result in context["results"]] == [
        conversation_id
    ]
    assert context["filters"].getlist("participant") == ["3"]


def test_message_validation_rejects_blank_and_over_limit(client, db):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Validation",
        participants=[2, 3],
    )
    _login(client, db, 2)

    blank = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": " \n\t"},
        headers={"Accept": "text/html"},
    )
    assert blank.status_code == 400

    too_long = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "x" * 5001},
        headers={"Accept": "text/html"},
    )
    assert too_long.status_code == 400
