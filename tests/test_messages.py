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


def _select_options(html: str, select_id: str) -> str:
    return html.split(f'id="{select_id}"', 1)[1].split("</select>", 1)[0]


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
            SELECT account_id, role
            FROM conversation_participant
            WHERE conversation_id = %s
            ORDER BY account_id
            """,
            (conversation["id"],),
        )
        assert cursor.fetchall() == [
            {"account_id": 1, "role": "owner"},
            {"account_id": 2, "role": "participant"},
            {"account_id": 3, "role": "participant"},
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


def test_participant_can_change_own_email_notification_preference(client, db):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Notification preference",
        participants=[3],
    )
    _login(client, db, 2)

    thread = client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
    assert thread.status_code == 200
    notification_input = thread.text.split('id="email_notifications"', 1)[1].split(
        ">", 1
    )[0]
    assert "checked" not in notification_input
    highlight_input = thread.text.split(
        'id="suppress_unread_highlight"', 1
    )[1].split(">", 1)[0]
    assert "checked" not in highlight_input
    pinned_input = thread.text.split('id="pinned"', 1)[1].split(">", 1)[0]
    assert "checked" not in pinned_input

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
    assert f'<a class="conversation-row" href="/messages/{conversation_id}">' in inbox.text
    assert '<span class="message-badge unread">1 unread</span>' in inbox.text
    assert '<i class="ph-fill ph-push-pin"></i> Pinned' in inbox.text
    assert inbox.text.index("Notification preference") < inbox.text.index(
        "Newer unpinned conversation"
    )

    thread = client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
    notification_input = thread.text.split('id="email_notifications"', 1)[1].split(
        ">", 1
    )[0]
    assert "checked" in notification_input
    highlight_input = thread.text.split(
        'id="suppress_unread_highlight"', 1
    )[1].split(">", 1)[0]
    assert "checked" in highlight_input
    pinned_input = thread.text.split('id="pinned"', 1)[1].split(">", 1)[0]
    assert "checked" in pinned_input


def test_unapproved_accounts_are_excluded_from_participant_selectors(client, db):
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
        compose_participants = _select_options(compose.text, "participant_available")
        assert '<option value="3"' not in compose_participants

        search = client.get("/messages/search", headers={"Accept": "text/html"})
        participant_filters = _select_options(search.text, "participant_available")
        assert '<option value="3"' not in participant_filters

        sender_filters = _select_options(search.text, "sender")
        assert '<option value="3"' in sender_filters
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE account SET approved = true WHERE id = 3")
        db.commit()


def test_search_accounts_are_scoped_to_interactions(client, app, db):
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
    user_sender_filters = _select_options(user_search.text, "sender")
    user_participant_filters = _select_options(user_search.text, "participant_available")
    assert '<option value="3"' in user_sender_filters
    assert '<option value="3"' in user_participant_filters
    assert '<option value="1"' not in user_sender_filters
    assert '<option value="1"' not in user_participant_filters

    admin_client = app.test_client()
    _login(admin_client, db, 1)
    admin_search = admin_client.get(
        "/messages/search",
        headers={"Accept": "text/html"},
    )
    admin_sender_filters = _select_options(admin_search.text, "sender")
    admin_participant_filters = _select_options(
        admin_search.text, "participant_available"
    )
    assert '<option value="2"' in admin_sender_filters
    assert '<option value="2"' in admin_participant_filters
    assert '<option value="3"' not in admin_sender_filters
    assert '<option value="3"' not in admin_participant_filters


def test_admin_messaging_interface_shows_only_shared_conversations(client, app, db):
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
    response = admin_client.get(
        "/admin/messages", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    assert "Shared with administrators" in response.text
    assert "Shared message" in response.text
    assert "Private user conversation" not in response.text
    assert '<span class="message-badge unread">1 unread</span>' in response.text
    assert 'href="/messages/new"' in response.text
    assert 'href="/messages"' in response.text

    admin_index = admin_client.get("/admin", headers={"Accept": "text/html"})
    assert 'href="/admin/messages" class="card card-attention"' in admin_index.text

    home = admin_client.get("/", headers={"Accept": "text/html"})
    assert 'href="/admin/" class="card card-attention"' in home.text

    compose = admin_client.get("/messages/new", headers={"Accept": "text/html"})
    assert 'href="/admin/messages"' in compose.text
    assert (
        '<button type="button" onclick="window.location.href=\'/admin/messages\'">'
        "Cancel</button>"
    ) in compose.text


def test_search_form_has_semantic_sections_and_date_controller(client, db):
    _login(client, db, 2)

    response = client.get(
        "/messages/search",
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert response.text.count('<fieldset class="grid">') == 3
    assert "Words and subject" in response.text
    assert "People" in response.text
    assert "Date" in response.text
    assert "data-date-mode" in response.text
    assert "data-date-single" in response.text
    assert "data-date-range" in response.text
    assert 'data-participant-required="false"' in response.text
    assert 'data-participant-transfer' in response.text
    assert "js/messages.js" in response.text
    assert '<option value="me"' in response.text
    assert "Me (bob)" in response.text
    assert "<h2>Search message history</h2>" not in response.text
    clear_button = (
        '<button type="button" onclick="window.location.href=\'/messages/search\'">'
        "Clear filters</button>"
    )
    assert clear_button in response.text
    assert ">Clear filters</a>" not in response.text

    compose = client.get("/messages/new", headers={"Accept": "text/html"})
    cancel_button = (
        '<button type="button" onclick="window.location.href=\'/messages\'">'
        "Cancel</button>"
    )
    assert cancel_button in compose.text
    assert '>Cancel</a>' not in compose.text
    assert "<h2>New conversation</h2>" not in compose.text

    inbox = client.get("/messages", headers={"Accept": "text/html"})
    assert "<h2>Your conversations</h2>" not in inbox.text


def test_conversation_forms_use_two_panel_participant_selector(client, db):
    _login(client, db, 2)

    compose = client.get("/messages/new", headers={"Accept": "text/html"})
    assert compose.status_code == 200
    assert 'data-participant-transfer' in compose.text
    assert 'data-participant-add disabled' in compose.text
    assert 'data-participant-remove disabled' in compose.text
    assert "js/messages.js" in compose.text

    available = _select_options(compose.text, "participant_available")
    selected = _select_options(compose.text, "participant_id")
    assert '<option value="1">alice</option>' in available
    assert '<option value="3">carol</option>' in available
    assert "<option" not in selected


def test_search_can_filter_messages_sent_by_current_user(client, db):
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
    assert "Message sent by current user" in response.text
    assert "Message sent by another user" not in response.text
    assert '<option value="me" selected>Me (bob)</option>' in response.text


def test_search_can_filter_system_messages(client, db):
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
    assert "Automated alert" in response.text
    assert "Human message" not in response.text
    assert '<option value="system" selected>System messages</option>' in response.text


def test_participant_and_shared_admin_access(client, app, db):
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
    response = client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    assert "Hello Carol" in response.text
    assert "<h2>Private first</h2>" not in response.text
    assert '<details class="thread-participants">' in response.text
    assert "<summary>All participants</summary>" in response.text
    assert "<details class=\"thread-participants\" open" not in response.text
    assert (
        '<p class="conversation-participants thread-participant-full-list">'
        in response.text
    )

    response = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "Hello Bob"},
    )
    assert response.status_code == 302

    admin_client = app.test_client()
    _login(admin_client, db, 1)
    response = admin_client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 404

    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE conversation SET admin_accessible = true WHERE id = %s",
            (conversation_id,),
        )
    db.commit()

    response = admin_client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
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


def test_unread_counts_are_per_user_and_threads_mark_messages_read(client, app, db):
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
    assert 'class="conversation-row unread"' in inbox.text
    assert '<span class="message-badge unread">2 unread</span>' in inbox.text
    assert (
        '<p class="conversation-preview">carol: Second unread message</p>'
        in inbox.text
    )

    home = client.get("/", headers={"Accept": "text/html"})
    assert '<a href="/member/" class="card card-attention"' in home.text
    member = client.get("/member", headers={"Accept": "text/html"})
    assert '<a href="/messages" class="card card-attention"' in member.text

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

    inbox = client.get("/messages", headers={"Accept": "text/html"})
    assert 'class="conversation-row unread"' not in inbox.text
    assert 'class="message-badge unread"' not in inbox.text
    home = client.get("/", headers={"Accept": "text/html"})
    assert '<a href="/member/" class="card card-attention"' not in home.text
    member = client.get("/member", headers={"Accept": "text/html"})
    assert '<a href="/messages" class="card card-attention"' not in member.text

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
    assert '<span class="message-badge unread">1 unread</span>' in inbox.text

    carol_client = app.test_client()
    _login(carol_client, db, 3)
    carol_inbox = carol_client.get("/messages", headers={"Accept": "text/html"})
    assert '<span class="message-badge unread">1 unread</span>' in carol_inbox.text


def test_shared_admin_unread_state_is_independent_for_each_admin(client, app, db):
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
        first_admin_inbox = client.get("/messages", headers={"Accept": "text/html"})
        assert '<span class="message-badge unread">1 unread</span>' in first_admin_inbox.text
        client.get(
            f"/messages/{conversation_id}",
            headers={"Accept": "text/html"},
        )

        second_admin = app.test_client()
        _login(second_admin, db, 3)
        second_admin_inbox = second_admin.get(
            "/messages",
            headers={"Accept": "text/html"},
        )
        assert '<span class="message-badge unread">1 unread</span>' in second_admin_inbox.text

        first_admin_inbox = client.get("/messages", headers={"Accept": "text/html"})
        assert 'class="message-badge unread"' not in first_admin_inbox.text
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE account SET role = 'user' WHERE id = 3")
        db.commit()


def test_owner_can_edit_subject_participants_and_admin_access(client, db):
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
    assert "The owner remains a participant." in edit_page.text
    participant_options = _select_options(edit_page.text, "participant_id")
    assert '<option value="2"' not in participant_options
    assert '<option value="3">carol</option>' in participant_options
    assert '<input type="hidden" name="participant_id" value="3">' in edit_page.text
    available_options = _select_options(edit_page.text, "participant_available")
    assert '<option value="1">alice</option>' in available_options
    assert '<option value="2"' not in available_options
    assert '<option value="3"' not in available_options

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
    assert "Updated subject" in thread.text
    assert (
        f'<a class="nav-item" href="/messages/{conversation_id}/edit">'
        '<i class="ph-fill ph-pencil-simple"></i> Edit conversation</a>'
    ) in thread.text


def test_non_owner_cannot_edit_user_conversation(client, db):
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
    assert f'href="/messages/{conversation_id}/edit"' not in thread.text

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


def test_invited_participant_can_leave_but_owner_cannot(client, db):
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
    assert f'action="/messages/{conversation_id}/leave"' in thread.text
    assert '<button type="submit">Leave conversation</button>' in thread.text
    assert '<details class="conversation-options">' in thread.text
    assert "<summary>Conversation options</summary>" in thread.text
    assert thread.text.index('class="reply-panel"') < thread.text.index(
        'class="conversation-options"'
    )

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
    assert f'action="/messages/{conversation_id}/leave"' not in owner_thread.text
    assert client.post(f"/messages/{conversation_id}/leave").status_code == 403


def test_disabling_admin_access_keeps_admin_as_an_ordinary_participant(
    client, app, db
):
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
        assert "Admin-created conversations remain shared" in edit_page.text

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


def test_message_alignment_and_sender_colour_hashes(client, db):
    conversation_id = _insert_conversation(
        db,
        owner_account_id=2,
        subject="Bubble styling",
        participants=[1, 2, 3],
        admin_accessible=True,
        participant_roles={1: "admin"},
    )
    with db.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (conversation_id, 2, "participant", "Own bubble"),
                (conversation_id, 3, "participant", "Other bubble"),
                (conversation_id, 1, "admin", "Moderator bubble"),
            ],
        )
    db.commit()
    _login(client, db, 2)

    response = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert (
        'class="message-row participant own"\n         '
        'style="--sender-colour: #d4735e"'
    ) in response.text
    assert "All conversations" not in response.text
    assert (
        'class="message-row participant other"\n         '
        'style="--sender-colour: #4e0740"'
    ) in response.text
    assert (
        'class="message-row admin other"\n         '
        'style="--sender-colour: #6b86b2"'
    ) in response.text
    assert response.text.count('class="message-avatar"') == 3
    assert 'src="/avatars/1"' in response.text
    assert 'src="/avatars/2"' in response.text
    assert 'src="/avatars/3"' in response.text
    assert "Administrator</span>" not in response.text


def test_user_can_hide_message_avatars(client, db):
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
    assert 'id="hide-message-avatars" name="hide_message_avatars"' in settings.text
    assert 'value="true" checked' in settings.text

    response = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 200
    assert 'class="message-avatar"' not in response.text

    client.post(
        "/settings",
        data={"theme": "auto"},
        headers={"Accept": "text/html"},
    )
    response = client.get(
        f"/messages/{conversation_id}",
        headers={"Accept": "text/html"},
    )
    assert 'class="message-avatar"' in response.text


def test_system_conversation_is_formatted_and_cannot_be_replied_to(client, db):
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
            (
                conversation_id,
                "Use [code]<safe>[/code]\n[pre]one\n  two[/pre]\n[o]overlined[/o]"
                "\n[bg=yellow][b]highlighted[/b][/bg]"
                "\n[c=white][bg=black]contrast[/bg][/c]"
                "\n[c=black][bg=white]inverse[/bg][/c]",
            ),
        )
    db.commit()
    _login(client, db, 2)

    response = client.get(
        f"/messages/{conversation_id}", headers={"Accept": "text/html"}
    )
    assert response.status_code == 200
    assert "<code>&lt;safe&gt;</code>" in response.text
    assert "<pre>one\n  two</pre>" in response.text
    assert '<span class="overline">overlined</span>' in response.text
    assert 'class="message-avatar"' not in response.text
    assert (
        '<span class="background-colour-yellow"><strong>highlighted</strong></span>'
        in response.text
    )
    assert (
        '<span class="colour-white"><span class="background-colour-black">'
        "contrast</span></span>"
    ) in response.text
    assert (
        '<span class="colour-black"><span class="background-colour-white">'
        "inverse</span></span>"
    ) in response.text
    assert "cannot be replied to" in response.text

    response = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "Attempted reply"},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 403


def test_search_filters_body_sender_participant_subject_and_exact_date(client, db):
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
    assert "Specific planning topic" in response.text
    assert "searchable needle" in response.text
    selected_participants = _select_options(response.text, "participant")
    assert '<option value="3">carol</option>' in selected_participants
    assert '<input type="hidden" name="participant" value="3">' in response.text


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
    assert "Message is required" in blank.text

    too_long = client.post(
        f"/messages/{conversation_id}/reply",
        data={"body": "x" * 5001},
        headers={"Accept": "text/html"},
    )
    assert too_long.status_code == 400
    assert "at most 5000 characters" in too_long.text
