import string

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.song_revisions import (
    create_song_revision,
    set_song_status,
    withdraw_song,
)


def _add_song(db, country="ES", *, year=2025, placeholder=False):
    with db.cursor() as cursor:
        entry_number = cursor.execute(
            """SELECT COALESCE(MAX(entry_number), 0) + 1 AS entry_number
               FROM song WHERE country_id = %s AND year_id = %s""",
            (country, year),
        ).fetchone()["entry_number"]
        song_id = cursor.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               VALUES (%s, %s, %s) RETURNING id""",
            (country, year, entry_number),
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id, sources
               ) VALUES (
                   %s, 2, 'Test Song', test_artist_credit('Test Artist'),
                   'https://example.test/source'
               )""",
            (song_id,),
        )
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=placeholder)
    db.commit()
    return song_id


def _current_data_id(db, song_id):
    return db.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,)).fetchone()[
        "song_data_id"
    ]


def test_verification_comments_append_in_order_with_the_authenticated_author(client, db, login):
    song_id = _add_song(db)
    db.execute(
        """INSERT INTO account (
               id, username, email, password, salt, approved, role
           ) VALUES (4, 'dana', 'dana@test', '\\x00', '\\x00', true, 'admin')
           ON CONFLICT (id) DO UPDATE SET role = 'admin'"""
    )
    db.commit()
    text = st.text(
        alphabet=string.ascii_letters + string.digits + " .,!?-\n",
        min_size=1,
        max_size=60,
    ).filter(lambda value: bool(value.strip()))

    @settings(max_examples=10, deadline=None)
    @given(comments=st.lists(st.tuples(st.sampled_from([1, 4]), text), min_size=1, max_size=5))
    def property_test(comments):
        db.execute(
            """DELETE FROM song_verification_comment
               WHERE song_data_id IN (
                   SELECT id FROM song_data WHERE song_id = %s
               )""",
            (song_id,),
        )
        db.commit()
        for author, body in comments:
            client.delete_cookie("session")
            login(author)
            response = client.post(
                f"/admin/manage/2025/verifications/{song_id}/comments",
                data={"comment": body},
            )
            assert response.status_code == 302

        rows = db.execute(
            """SELECT comment.author_id, comment.body
               FROM song_verification_comment AS comment
               JOIN song_data AS data ON data.id = comment.song_data_id
               WHERE data.song_id = %s ORDER BY comment.id""",
            (song_id,),
        ).fetchall()
        assert rows == [{"author_id": author, "body": body.strip()} for author, body in comments]

    property_test()


def test_comment_changes_require_the_author_and_preserve_other_comments(client, db, login):
    db.execute("UPDATE account SET role = 'editor' WHERE id = 3")
    db.execute(
        """INSERT INTO year (id, status, special_name, special_short_name)
           VALUES (-812, 'open', 'Comment test', 'comment-test') ON CONFLICT DO NOTHING"""
    )
    db.commit()
    songs = {year: _add_song(db, year=year) for year in (2025, -812)}
    text = st.text(alphabet=string.ascii_letters + " \n", min_size=1, max_size=80)

    @settings(max_examples=50, deadline=None)
    @given(
        year=st.sampled_from([2025, -812]),
        author=st.sampled_from([1, 3]),
        actor=st.sampled_from([None, 1, 2, 3]),
        action=st.sampled_from(["edit", "delete"]),
        body=st.one_of(text, st.sampled_from(["", " \n ", "x" * 2001])),
        wrong_year=st.booleans(),
        historical=st.booleans(),
    )
    def property_test(year, author, actor, action, body, wrong_year, historical):
        song_id = songs[year]
        data_id = _current_data_id(db, song_id)
        if historical:
            with db.cursor() as cursor:
                create_song_revision(
                    cursor, song_id, {"title": f"Updated title {data_id}"}, changed_by=1
                )
        comment_ids = [
            db.execute(
                """INSERT INTO song_verification_comment (song_data_id, author_id, body)
                   VALUES (%s, %s, %s) RETURNING id""",
                (data_id, author, original),
            ).fetchone()["id"]
            for original in ("Original comment", "Other comment")
        ]
        db.commit()
        before = db.execute(
            "SELECT * FROM song_verification_comment WHERE id = ANY(%s) ORDER BY id",
            (comment_ids,),
        ).fetchall()
        client.delete_cookie("session")
        if actor is not None:
            login(actor)
        base = "/admin/manage/2025" if year == 2025 else "/admin/manage/special/comment-test"
        if wrong_year:
            base = "/admin/manage/2024"
        try:
            response = client.post(
                f"{base}/verifications/comments/{comment_ids[0]}",
                data={"action": action, "comment": body},
            )
            valid_body = action == "delete" or 1 <= len(body.strip()) <= 2000
            allowed = actor == author and not wrong_year and valid_body
            if actor in {1, 3}:
                assert response.status_code == (
                    400 if not valid_body else 302 if allowed else 404
                )
            else:
                assert response.status_code == 302
            after = db.execute(
                "SELECT * FROM song_verification_comment WHERE id = ANY(%s) ORDER BY id",
                (comment_ids,),
            ).fetchall()
            expected = [dict(row) for row in before]
            if allowed:
                if action == "delete":
                    expected.pop(0)
                else:
                    expected[0]["body"] = body.strip()
            assert after == expected
            if actor in {1, 3}:
                assert client.get(f"{base}/verifications").status_code == 200
        finally:
            db.execute("DELETE FROM song_verification_comment WHERE id = ANY(%s)", (comment_ids,))
            db.commit()

    property_test()


def test_moderation_state_changes_are_song_level_and_message_gated(client, db, login):
    login(1)
    message = st.text(
        alphabet=string.ascii_letters + string.digits + " .,!?-",
        min_size=1,
        max_size=60,
    ).filter(lambda value: bool(value.strip()))

    @settings(max_examples=12, deadline=None)
    @given(
        status=st.sampled_from(
            ["pending", "pending-second-opinion", "accepted", "rejected", "more-info"]
        ),
        body=message,
        country_connection_confirmed=st.booleans(),
    )
    def property_test(status, body, country_connection_confirmed):
        song_id = _add_song(db)
        revision_count = db.execute(
            "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s", (song_id,)
        ).fetchone()["count"]
        response = client.post(
            f"/admin/manage/2025/verifications/{song_id}/status",
            data={
                "status": status,
                "message": body,
                "country_connection_confirmed": (
                    "yes" if country_connection_confirmed else ""
                ),
            },
        )
        accepted = status != "accepted" or country_connection_confirmed
        assert response.status_code == (302 if accepted else 400)
        current = db.execute(
            "SELECT approval_status FROM current_song WHERE id = %s", (song_id,)
        ).fetchone()
        assert current["approval_status"] == (status if accepted else "pending")
        assert (
            db.execute(
                "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s", (song_id,)
            ).fetchone()["count"]
            == revision_count
        )
        notifications = db.execute(
            """SELECT message.body
               FROM conversation
               JOIN message ON message.conversation_id = conversation.id
               WHERE conversation.metadata->>'submitter_id' = '2'
                 AND message.body = %s""",
            (body.strip(),),
        ).fetchall()
        assert len(notifications) == int(accepted and status in {"rejected", "more-info"})

    property_test()

    song_id = _add_song(db)

    @given(
        status=st.sampled_from(["rejected", "more-info"]),
        whitespace=st.sampled_from(["", " ", "\n\t"]),
    )
    def missing_message_property(status, whitespace):
        response = client.post(
            f"/admin/manage/2025/verifications/{song_id}/status",
            data={"status": status, "message": whitespace},
        )
        assert response.status_code == 400
        assert (
            db.execute(
                "SELECT approval_status FROM current_song WHERE id = %s", (song_id,)
            ).fetchone()["approval_status"]
            == "pending"
        )

    missing_message_property()


def test_replacing_song_content_resets_verification_while_metadata_edits_preserve_it(
    db,
):
    @settings(max_examples=12, deadline=None)
    @given(
        status=st.sampled_from(
            ["pending-second-opinion", "accepted", "rejected", "more-info"]
        ),
        change_kind=st.sampled_from(["title", "artist", "metadata"]),
        value=st.text(
            alphabet=string.ascii_letters + string.digits + " -_.,!?",
            min_size=1,
            max_size=40,
        ).filter(
            lambda value: bool(value.strip())
            and value.strip().casefold() not in {"test song", "test artist"}
        ),
    )
    def property_test(status, change_kind, value):
        song_id = _add_song(db)
        with db.cursor() as cursor:
            set_song_status(cursor, song_id, changed_by=1, approval_status=status)
            if change_kind == "artist":
                artist_credit_set_id = cursor.execute(
                    "SELECT test_artist_credit(%s) AS id", (value,)
                ).fetchone()["id"]
                changes = {"artist_credit_set_id": artist_credit_set_id}
            else:
                changes = {
                    "title" if change_kind == "title" else "notes": value
                }
            create_song_revision(cursor, song_id, changes, changed_by=2)
        db.commit()

        current = db.execute(
            "SELECT approval_status FROM current_song WHERE id = %s", (song_id,)
        ).fetchone()
        expected = status if change_kind == "metadata" else "pending"
        assert current["approval_status"] == expected

    property_test()


def test_historical_revisions_can_be_merged_or_hidden_without_losing_comments(client, db, login):
    login(1)

    @settings(max_examples=9, deadline=None)
    @given(action=st.sampled_from(["merge", "hide-replaced", "hide-withdrawn"]))
    def property_test(action):
        song_id = _add_song(db)
        historical_id = _current_data_id(db, song_id)
        db.execute(
            """INSERT INTO song_verification_comment (
                   song_data_id, author_id, body
               ) VALUES (%s, 1, 'Historical review')""",
            (historical_id,),
        )
        with db.cursor() as cursor:
            if action == "hide-withdrawn":
                withdraw_song(cursor, song_id, changed_by=2)
            else:
                create_song_revision(cursor, song_id, {"title": "Replacement"}, changed_by=2)
        db.commit()

        endpoint = "merge" if action == "merge" else "hide"
        response = client.post(f"/admin/manage/2025/verifications/{historical_id}/{endpoint}")
        assert response.status_code == 302
        if action == "merge":
            current_id = _current_data_id(db, song_id)
            merge = db.execute(
                """SELECT merged_into_song_data_id, merged_by
                   FROM song_revision_merge WHERE song_data_id = %s""",
                (historical_id,),
            ).fetchone()
            assert merge == {
                "merged_into_song_data_id": current_id,
                "merged_by": 1,
            }
            comment_data_id = current_id
        else:
            hidden = db.execute(
                """SELECT hidden_by FROM song_verification_hidden_revision
                   WHERE song_data_id = %s""",
                (historical_id,),
            ).fetchone()
            assert hidden == {"hidden_by": 1}
            comment_data_id = historical_id
        assert db.execute(
            """SELECT song_data_id FROM song_verification_comment
               WHERE body = 'Historical review' AND song_data_id = %s""",
            (comment_data_id,),
        ).fetchone() == {"song_data_id": comment_data_id}

    property_test()


def test_verification_access_and_comment_scope_follow_role_and_managed_year(client, db, login):
    wrong_year_song = _add_song(db, year=2024)

    @given(role=st.sampled_from(["anonymous", "user", "editor", "admin"]))
    def property_test(role):
        client.delete_cookie("session")
        if role != "anonymous":
            user_id = {"user": 2, "editor": 3, "admin": 1}[role]
            db.execute("UPDATE account SET role = %s WHERE id = %s", (role, user_id))
            db.commit()
            login(user_id)
        page = client.get("/admin/manage/2025/verifications")
        assert page.status_code == (200 if role in {"editor", "admin"} else 302)
        if role in {"editor", "admin"}:
            response = client.post(
                f"/admin/manage/2025/verifications/{wrong_year_song}/comments",
                data={"comment": "Wrong year"},
            )
            assert response.status_code == 404

    property_test()
    assert (
        db.execute(
            """SELECT COUNT(*) AS count
               FROM song_verification_comment AS comment
               JOIN song_data AS data ON data.id = comment.song_data_id
               WHERE data.song_id = %s""",
            (wrong_year_song,),
        ).fetchone()["count"]
        == 0
    )
