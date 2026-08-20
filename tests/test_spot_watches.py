from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.song_revisions import set_song_status


def _create_api_song(client, headers, country="US", placeholder=False):
    response = client.post(
        "/api/song",
        headers=headers,
        json={
            "year": 2025,
            "country": country,
            "title": f"{country} watched entry",
            "artist": "Watched Artist",
            "sources": "https://example.test/source",
            "languages": [20],
            "is_placeholder": placeholder,
        },
    )
    assert response.status_code == 201
    return response.get_json()["result"]


def _insert_song(db, country, submitter, *, placeholder):
    entry_number = db.execute(
        """SELECT COALESCE(MAX(entry_number), 0) + 1 AS entry_number
           FROM song WHERE year_id = 2025 AND country_id = %s""",
        (country,),
    ).fetchone()["entry_number"]
    with db.cursor() as cursor:
        song_id = cursor.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               VALUES (%s, 2025, %s) RETURNING id""",
            (country, entry_number),
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id, sources
               ) VALUES (
                   %s, %s, %s, test_artist_credit('Watched Artist'),
                   'https://example.test/source'
               )""",
            (song_id, submitter, f"Watched entry {song_id}"),
        )
        set_song_status(cursor, song_id, changed_by=submitter, is_placeholder=placeholder)
    db.commit()
    return song_id, entry_number


def test_watch_state_follows_action_and_submission_availability(client, db, bob_headers, login):
    song = _create_api_song(client, bob_headers)
    entry_number = song.get("entry_number") or 1
    login(3)

    @given(
        initially_watching=st.booleans(),
        submissions_open=st.booleans(),
        action=st.sampled_from(["watch", "unwatch"]),
    )
    def property_test(initially_watching, submissions_open, action):
        db.execute(
            """DELETE FROM year_spot_watch
               WHERE account_id = 3 AND year_id = 2025 AND country_id = 'US'"""
        )
        if initially_watching:
            db.execute(
                """INSERT INTO year_spot_watch (
                       account_id, year_id, country_id, entry_number
                   ) VALUES (3, 2025, 'US', %s)""",
                (entry_number,),
            )
        db.execute(
            "UPDATE year SET submissions_open = %s WHERE id = 2025",
            (submissions_open,),
        )
        db.commit()

        response = client.post(
            "/year/2025/spot-watch",
            data={
                "country_id": "US",
                "entry_number": str(entry_number),
                "action": action,
            },
        )

        blocked = action == "watch" and not submissions_open
        assert response.status_code == (403 if blocked else 302)
        watching = db.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM year_spot_watch
                   WHERE account_id = 3 AND year_id = 2025
                     AND country_id = 'US' AND entry_number = %s
               ) AS watching""",
            (entry_number,),
        ).fetchone()["watching"]
        expected = initially_watching if blocked else action == "watch"
        assert watching is expected

    try:
        property_test()
    finally:
        db.execute("UPDATE year SET submissions_open = true WHERE id = 2025")
        db.commit()


def test_watchers_receive_one_private_notification_when_a_spot_becomes_unavailable(
    client, app, db, bob_headers, configured_email
):
    @settings(max_examples=6)
    @given(event=st.sampled_from(["placeholder", "deleted"]))
    def property_test(event):
        song_id, entry_number = _insert_song(db, "US", 2, placeholder=False)
        db.execute(
            """INSERT INTO year_spot_watch (
                   account_id, year_id, country_id, entry_number
               ) VALUES (3, 2025, 'US', %s)""",
            (entry_number,),
        )
        db.commit()
        app.extensions["mail_outbox"].clear()

        response = (
            client.patch(
                f"/api/song/{song_id}",
                headers=bob_headers,
                json={"is_placeholder": True},
            )
            if event == "placeholder"
            else client.delete(f"/api/song/{song_id}", headers=bob_headers)
        )

        assert response.status_code == (200 if event == "placeholder" else 204)
        notification = db.execute(
            """SELECT conversation.system_conversation,
                      participant.account_id, participant.email_notifications,
                      message.sender_kind
               FROM conversation
               JOIN conversation_participant AS participant
                 ON participant.conversation_id = conversation.id
               JOIN message ON message.conversation_id = conversation.id
               WHERE conversation.metadata->>'spot_watch_event' = %s
                 AND conversation.metadata->>'entry_number' = %s
               ORDER BY conversation.id DESC LIMIT 1""",
            (event, str(entry_number)),
        ).fetchone()
        assert notification == {
            "system_conversation": True,
            "account_id": 3,
            "email_notifications": True,
            "sender_kind": "system",
        }
        assert [message["To"] for message in app.extensions["mail_outbox"]] == ["carol@test"]

    property_test()


def test_occupying_a_spot_removes_the_submitters_obsolete_watch(client, db, bob_headers):
    @settings(max_examples=8)
    @given(operation=st.sampled_from(["submit", "claim"]))
    def property_test(operation):
        country = "US" if operation == "submit" else "ES"
        db.execute(
            """INSERT INTO year_spot_watch (
                   account_id, year_id, country_id, entry_number
               ) VALUES (2, 2025, %s, 1)
               ON CONFLICT DO NOTHING""",
            (country,),
        )
        db.commit()

        if operation == "submit":
            response = client.post(
                "/api/song",
                headers=bob_headers,
                json={
                    "year": 2025,
                    "country": country,
                    "title": "Submitted placeholder",
                    "artist": "Placeholder Artist",
                    "sources": "https://example.test/submitted",
                    "languages": [20],
                    "is_placeholder": True,
                },
            )
        else:
            song_id, _entry_number = _insert_song(db, country, 3, placeholder=True)
            response = client.put(
                f"/api/song/{song_id}",
                headers=bob_headers,
                json={
                    "title": "Claimed placeholder",
                    "artist": "Placeholder Artist",
                    "sources": "https://example.test/claimed",
                    "languages": [20],
                    "is_placeholder": True,
                },
            )

        assert response.status_code in {200, 201}
        remaining = db.execute(
            """SELECT COUNT(*) AS count FROM year_spot_watch
               WHERE account_id = 2 AND year_id = 2025 AND country_id = %s""",
            (country,),
        ).fetchone()["count"]
        assert remaining == 0

    property_test()
