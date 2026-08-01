import uuid

from world_stage.utils.song_revisions import set_song_status

HTML_HEADERS = {"Accept": "text/html"}


def _login(client, db, user_id: int) -> str:
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
    return session_id


def _add_song(
    db,
    country: str,
    *,
    sources: str | None = None,
    year: int = 2025,
    placeholder: bool = False,
) -> int:
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO song (country_id, year_id)
            VALUES (%s, %s)
            RETURNING id
            """,
            (country, year),
        )
        song_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO song_data (
                song_id, submitter_id, title, artist, sources
            ) VALUES (%s, 2, 'Test Song', 'Test Artist', %s)
            """,
            (song_id, sources),
        )
        set_song_status(
            cursor, song_id, changed_by=2, is_placeholder=placeholder
        )
    db.commit()
    return song_id


def _revise_song(db, song_id: int, **changes) -> int:
    from world_stage.utils.song_revisions import create_song_revision

    with db.cursor() as cursor:
        revision_id = create_song_revision(
            cursor, song_id, changes, changed_by=2
        )["id"]
    db.commit()
    return revision_id


def test_verification_page_lists_all_entries_and_sources(client, db):
    _login(client, db, 1)
    first_song = _add_song(
        db,
        "ES",
        sources="https://example.com/release\nhttps://example.com/originality",
    )
    second_song = _add_song(db, "FR")

    response = client.get("/admin/manage/2025/verifications", headers=HTML_HEADERS)

    assert response.status_code == 200
    assert f'id="song-{first_song}"' in response.text
    assert f'id="song-{second_song}"' in response.text
    assert "Spain" in response.text
    assert "France" in response.text
    assert response.text.count("Submitter: bob") == 2
    assert 'href="https://example.com/release"' in response.text
    assert "No sources specified" in response.text


def test_replaced_revision_is_listed_without_review_history(client, db):
    _login(client, db, 1)
    song_id = _add_song(db, "ES")
    old_version_id = _revise_song(db, song_id, sources="Corrected source")
    _revise_song(db, song_id, artist="Replacement Artist")

    response = client.get(
        "/admin/manage/2025/verifications", headers=HTML_HEADERS
    )

    assert response.status_code == 200
    assert f'id="historical-song-{old_version_id}"' in response.text
    assert "Replacement Artist" in response.text


def test_multiple_comments_are_appended_and_attributed_to_each_moderator(client, db):
    song_id = _add_song(db, "ES", sources="https://example.com")
    _login(client, db, 1)

    first = client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "Release date checks out.\n\nSecond paragraph."},
    )
    second = client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "Originality source also checked."},
    )

    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO account (
                id, username, email, password, salt, approved, role
            )
            VALUES (4, 'dana', 'dana@test', '\\x00', '\\x00', true, 'admin')
            ON CONFLICT (id) DO UPDATE SET role = 'admin'
            """
        )
    db.commit()
    _login(client, db, 4)
    third = client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "Confirmed independently."},
    )

    assert first.status_code == 302
    assert second.status_code == 302
    assert third.status_code == 302
    assert first.headers["Location"].endswith(f"#song-{song_id}")

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT author_id, body
            FROM song_verification_comment
            JOIN song_data ON song_data.id = song_verification_comment.song_data_id
            WHERE song_data.song_id = %s
            ORDER BY song_verification_comment.id
            """,
            (song_id,),
        )
        assert cursor.fetchall() == [
            {
                "author_id": 1,
                "body": "Release date checks out.\n\nSecond paragraph.",
            },
            {"author_id": 1, "body": "Originality source also checked."},
            {"author_id": 4, "body": "Confirmed independently."},
        ]


def test_moderator_can_set_all_four_verification_states(client, db):
    song_id = _add_song(db, "ES", sources="https://example.com")
    _login(client, db, 1)

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s",
            (song_id,),
        )
        revision_count = cursor.fetchone()["count"]

    verify = client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={"status": "accepted"},
    )
    assert verify.status_code == 302

    with db.cursor() as cursor:
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "accepted"

    reject = client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={"status": "rejected", "message": "The source is not sufficient."},
    )
    assert reject.status_code == 302

    with db.cursor() as cursor:
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "rejected"

    more_info = client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={"status": "more-info", "message": "Please provide another source."},
    )
    assert more_info.status_code == 302

    with db.cursor() as cursor:
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "more-info"

    reset = client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={"status": "pending"},
    )
    assert reset.status_code == 302

    with db.cursor() as cursor:
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "pending"
        cursor.execute(
            "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s",
            (song_id,),
        )
        assert cursor.fetchone()["count"] == revision_count
        cursor.execute(
            """
            SELECT approval_status AS status, changed_by AS moderator_id
            FROM song_status
            WHERE song_id = %s AND changed_by = 1
            ORDER BY id
            """,
            (song_id,),
        )
        assert cursor.fetchall() == [
            {"status": "accepted", "moderator_id": 1},
            {"status": "rejected", "moderator_id": 1},
            {"status": "more-info", "moderator_id": 1},
            {"status": "pending", "moderator_id": 1},
        ]


def test_rejection_and_more_info_require_a_submitter_message(client, db):
    song_id = _add_song(db, "ES", sources="https://example.com")
    _login(client, db, 1)

    for status in ("rejected", "more-info"):
        response = client.post(
            f"/admin/manage/2025/verifications/{song_id}/status",
            data={"status": status, "message": "  "},
            headers=HTML_HEADERS,
        )
        assert response.status_code == 400
        assert "A message to the submitter is required" in response.text

    with db.cursor() as cursor:
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "pending"
        cursor.execute("SELECT COUNT(*) AS count FROM conversation")
        assert cursor.fetchone()["count"] == 0


def test_rejection_notifies_submitter_and_displays_home_banner(client, db):
    song_id = _add_song(db, "ES", sources="https://example.com")
    _login(client, db, 1)

    response = client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={
            "status": "rejected",
            "message": "The release source does not establish eligibility.",
        },
    )
    assert response.status_code == 302

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, subject, metadata, admin_accessible, created_by_admin
            FROM conversation
            """
        )
        conversation = cursor.fetchone()
        assert conversation["subject"] == "Song submission rejected: Test Artist – Test Song"
        assert conversation["metadata"] == {
            "banner": True,
            "submitter_id": 2,
        }
        assert conversation["admin_accessible"] is True
        assert conversation["created_by_admin"] is True

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
            "sender_id": 1,
            "sender_kind": "admin",
            "body": "The release source does not establish eligibility.",
        }

    moderator_home = client.get("/", headers=HTML_HEADERS)
    assert "Song submission rejected: Test Artist – Test Song" not in moderator_home.text

    _login(client, db, 2)
    home = client.get("/", headers=HTML_HEADERS)
    assert "Song submission rejected: Test Artist – Test Song" in home.text
    assert f'href="/messages/{conversation["id"]}"' in home.text

    thread = client.get(f'/messages/{conversation["id"]}', headers=HTML_HEADERS)
    assert "The release source does not establish eligibility." in thread.text


def test_comments_follow_minor_edits_while_status_remains_song_level(
    client, db
):
    song_id = _add_song(db, "ES", sources="https://example.com")
    _login(client, db, 1)
    client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "Original review."},
    )
    client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={"status": "accepted"},
    )

    with db.cursor() as cursor:
        cursor.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,))
        original_version_id = cursor.fetchone()["song_data_id"]

    _revise_song(db, song_id, artist="TEST ARTIST", title="test song")
    _revise_song(db, song_id, sources="https://example.com/corrected-source")

    with db.cursor() as cursor:
        cursor.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,))
        replaced_version_id = cursor.fetchone()["song_data_id"]
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "accepted"
    _revise_song(db, song_id, artist="Different Artist")

    from world_stage.utils.song_revisions import withdraw_song

    with db.cursor() as cursor:
        withdraw_song(cursor, song_id, changed_by=2)
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT song_data_id
            FROM song_verification_comment
            """,
        )
        assert cursor.fetchone()["song_data_id"] == replaced_version_id
        cursor.execute("SELECT DISTINCT song_data_id FROM song_status")
        assert cursor.fetchall() == [{"song_data_id": original_version_id}]
        cursor.execute(
            """
            SELECT title IS NULL OR artist IS NULL AS song_deleted FROM song_data
            WHERE country_id = 'ES' AND year_id = 2025 ORDER BY id
            """,
        )
        assert [row["song_deleted"] for row in cursor.fetchall()] == [
            False, False, False, False, True
        ]


def test_moderator_can_merge_a_replaced_song_back(client, db):
    song_id = _add_song(db, "ES", sources="https://example.com")
    _login(client, db, 1)
    client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "Still applies after the correction."},
    )
    client.post(
        f"/admin/manage/2025/verifications/{song_id}/status",
        data={"status": "accepted"},
    )
    with db.cursor() as cursor:
        cursor.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,))
        version_id = cursor.fetchone()["song_data_id"]
    current_version_id = _revise_song(db, song_id, artist="Test Artist feat. Guest")

    with db.cursor() as cursor:
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "accepted"

    response = client.post(
        f"/admin/manage/2025/verifications/{version_id}/merge",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"#song-{song_id}")

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT song_data_id
            FROM song_verification_comment
            """,
        )
        assert cursor.fetchone()["song_data_id"] == current_version_id
        cursor.execute("SELECT approval_status FROM current_song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["approval_status"] == "accepted"


def test_comment_must_target_a_song_in_the_managed_year(client, db):
    _login(client, db, 1)
    song_id = _add_song(db, "ES", year=2024)

    response = client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "This belongs to another year."},
    )

    assert response.status_code == 404
    with db.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*) AS count FROM song_verification_comment AS comment
               JOIN song_data AS data ON data.id = comment.song_data_id
               WHERE data.song_id = %s""",
            (song_id,),
        )
        assert cursor.fetchone()["count"] == 0


def test_deleted_extra_placeholder_keeps_tuple_history_and_comments(
    client, db, alice_headers
):
    song_id = _add_song(
        db, "ES", sources="Placeholder planning source", placeholder=True
    )
    _login(client, db, 1)
    client.post(
        f"/admin/manage/2025/verifications/{song_id}/comments",
        data={"comment": "Keep this planning note."},
    )

    response = client.delete(f"/api/song/{song_id}", headers=alice_headers)
    assert response.status_code == 204

    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["count"] == 1
        cursor.execute(
            """
            SELECT data.song_id, data.country_id, data.year_id, data.entry_number
            FROM song_data AS data
            JOIN song_verification_comment AS comment
              ON comment.song_data_id = data.id
            WHERE comment.body = 'Keep this planning note.'
            """
        )
        preserved = cursor.fetchone()
        assert preserved == {
            "song_id": song_id,
            "country_id": "ES",
            "year_id": 2025,
            "entry_number": 1,
        }

def test_verifications_require_admin_access(client):
    response = client.get("/admin/manage/2025/verifications", headers=HTML_HEADERS)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
