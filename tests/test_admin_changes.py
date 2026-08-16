import json
import uuid
from datetime import datetime
from pathlib import Path

from flask import template_rendered

from world_stage.utils.song_revisions import (
    create_song_revision,
    set_song_status,
    withdraw_song,
)


def _add_song(db, *, title="Original title", artist="Original artist") -> int:
    with db.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_user_id', '2', false)")
        cursor.execute(
            """
            WITH inserted AS (
                INSERT INTO song (country_id, year_id, entry_number)
                VALUES ('ES', 2025, 1)
                RETURNING id
            )
            INSERT INTO song_data (
                song_id, submitter_id, title, artist_credit_set_id
            )
            SELECT id, 2, %s, test_artist_credit(%s) FROM inserted
            RETURNING song_id
            """,
            (title, artist),
        )
        song_id = cursor.fetchone()["song_id"]
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=False)
    db.commit()
    return song_id


def _login_admin(client, db) -> None:
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (1, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')
            """,
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", session_id)


def _get_changes(client, **query_string):
    rendered = []

    def capture(_sender, template, context, **_extra):
        if template.name == "admin/changes.html":
            rendered.append(context)

    with template_rendered.connected_to(capture, client.application):
        response = client.get(
            "/admin/changes",
            query_string=query_string,
            headers={"Accept": "text/html"},
        )

    assert rendered
    return response, rendered[0]


def test_song_changes_are_derived_from_adjacent_revisions(db):
    song_id = _add_song(db)
    with db.cursor() as cursor:
        create_song_revision(cursor, song_id, {"notes": "Metadata correction"}, changed_by=2)
        cursor.execute("SELECT test_artist_credit('ORIGINAL ARTIST') AS id")
        create_song_revision(
            cursor,
            song_id,
            {"artist_credit_set_id": cursor.fetchone()["id"]},
            changed_by=2,
        )
        create_song_revision(cursor, song_id, {"title": "Replacement title"}, changed_by=2)
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=True)
        create_song_revision(cursor, song_id, {"submitter_id": 3}, changed_by=2)
        withdraw_song(cursor, song_id, changed_by=2)
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type, changed_by, changed_fields
            FROM song_change
            WHERE song_country_id = 'ES' AND song_year_id = 2025
            ORDER BY id
            """
        )
        changes = cursor.fetchall()

    assert [change["event_type"] for change in changes] == [
        "create",
        "song_modification",
        "song_modification",
        "song_replacement",
        "ownership_change",
        "delete",
    ]
    assert all(change["changed_by"] == 2 for change in changes)
    assert changes[1]["changed_fields"]["notes"] == {
        "old": None,
        "new": "Metadata correction",
    }
    assert changes[2]["changed_fields"] is None
    assert changes[3]["changed_fields"]["title"] == {
        "old": "Original title",
        "new": "Replacement title",
    }


def test_moving_then_replacing_preserves_old_revision_country(db):
    song_id = _add_song(db, title="North Macedonian entry")
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE song SET country_id = 'FR' WHERE id = %s",
            (song_id,),
        )

        # The moved entry remains readable before its replacement is saved.
        cursor.execute(
            "SELECT country_id, title FROM current_song WHERE id = %s",
            (song_id,),
        )
        moved = cursor.fetchone()
        assert moved == {
            "country_id": "FR",
            "title": "North Macedonian entry",
        }

        create_song_revision(
            cursor,
            song_id,
            {"title": "Algerian entry"},
            changed_by=2,
        )
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT country_id, title
            FROM song_data
            WHERE song_id = %s
            ORDER BY created_at, id
            """,
            (song_id,),
        )
        revisions = cursor.fetchall()
        cursor.execute(
            "SELECT country_id, title FROM current_song WHERE id = %s",
            (song_id,),
        )
        current = cursor.fetchone()

    assert revisions == [
        {"country_id": "ES", "title": "North Macedonian entry"},
        {"country_id": "FR", "title": "Algerian entry"},
    ]
    assert current == {"country_id": "FR", "title": "Algerian entry"}


def test_refilling_a_withdrawn_song_is_a_creation(db):
    song_id = _add_song(db)
    with db.cursor() as cursor:
        withdraw_song(cursor, song_id, changed_by=2)
        cursor.execute(
            """
            INSERT INTO song_data (
                song_id, submitter_id, title, artist_credit_set_id, changed_by
            ) VALUES (
                %s, 2, 'Returned title', test_artist_credit('Returned artist'), 2
            )
            """,
            (song_id,),
        )
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT event_type FROM song_change WHERE song_id = %s ORDER BY id",
            (song_id,),
        )
        event_types = [row["event_type"] for row in cursor.fetchall()]

    assert event_types == ["create", "delete", "create"]


def test_deletion_and_creation_are_derived_from_required_identity_fields(db):
    song_id = _add_song(db)
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO song_data (
                song_id, title, artist_credit_set_id, changed_by
            )
            VALUES (%s, NULL, NULL, 2)
            """,
            (song_id,),
        )
        cursor.execute(
            """
            INSERT INTO song_data (
                song_id, title, artist_credit_set_id, modified_at, changed_by
            ) VALUES (
                %s, 'Restored', test_artist_credit('Restored Artist'), NULL, 2
            )
            """,
            (song_id,),
        )
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT event_type FROM song_change WHERE song_id = %s ORDER BY id",
            (song_id,),
        )
        event_types = [row["event_type"] for row in cursor.fetchall()]
        cursor.execute("SELECT title, artist FROM current_song WHERE id = %s", (song_id,))
        current = cursor.fetchone()

    assert event_types == ["create", "delete", "create"]
    assert current == {"title": "Restored", "artist": "Restored Artist"}


def test_consolidated_migration_reconstructs_legacy_audit_rows(db):
    song_id = _add_song(db, title="New title", artist="Original artist")
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM song_status WHERE song_id = %s", (song_id,))
        cursor.execute(
            """
            UPDATE song_data
            SET notes = 'Updated notes', submitter_id = 3
            WHERE song_id = %s
            """,
            (song_id,),
        )
        cursor.execute(
            """
            CREATE TEMP TABLE song_audit_log (
                id bigint PRIMARY KEY,
                song_id bigint,
                event_type text NOT NULL,
                changed_by bigint,
                changed_at timestamptz NOT NULL,
                song_title text,
                song_artist text,
                song_country_id text,
                song_year_id bigint,
                changed_fields jsonb
            ) ON COMMIT DROP
            """
        )
        cursor.execute(
            """
            INSERT INTO song_audit_log VALUES
                (1, %s, 'create', 2, '2025-01-01 12:00:00+00',
                 'Old title', 'Original artist', 'ES', 2025, NULL),
                (2, %s, 'song_replacement', 2, '2025-02-01 12:00:00+00',
                 'New title', 'Original artist', 'ES', 2025,
                 '{"title":{"old":"Old title","new":"New title"},
                   "artist":{"old":"Original artist","new":"Original artist"}}'),
                (3, %s, 'placeholder_on', 2, '2025-02-01 12:00:00+00',
                 'New title', 'Original artist', 'ES', 2025, NULL),
                (4, %s, 'ownership_change', 2, '2025-02-01 12:00:00+00',
                 'New title', 'Original artist', 'ES', 2025,
                 '{"submitter_id":{"old":"2","new":"3"}}'),
                (5, %s, 'song_modification', 2, '2025-02-01 12:00:00+00',
                 'New title', 'Original artist', 'ES', 2025,
                 '{"notes":{"old":null,"new":"Updated notes"}}'),
                (10, 9999, 'create', 2, '2024-01-01 12:00:00+00',
                 'Deleted song', 'Past artist', 'US', 2025, NULL),
                (11, 9999, 'song_modification', 2, '2024-02-01 12:00:00+00',
                 'Deleted song', 'Past artist', 'US', 2025,
                 '{"notes":{"old":null,"new":"Historical note"}}'),
                (12, 9999, 'delete', 2, '2024-03-01 12:00:00+00',
                 'Deleted song', 'Past artist', 'US', 2025, NULL)
            """,
            (song_id, song_id, song_id, song_id, song_id),
        )

        migration = (
            Path(__file__).parents[1]
            / "world_stage/migrations/20260801130000_add_song_verification_revisions.sql"
        ).read_text()
        reconstruction = migration.split("-- BEGIN LEGACY SONG AUDIT RECONSTRUCTION", 1)[1].split(
            "-- END LEGACY SONG AUDIT RECONSTRUCTION", 1
        )[0]
        reconstruction = reconstruction.replace(
            "submitter_id, title, artist, created_at",
            "submitter_id, title, artist_credit_set_id, created_at",
        ).replace(
            "state ->> 'title', state ->> 'artist', revision_at",
            "state ->> 'title', test_artist_credit(state ->> 'artist'), revision_at",
        )
        cursor.execute(reconstruction)
    db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type, changed_by, song_title, changed_fields
            FROM song_change
            WHERE song_id = %s
            ORDER BY id
            """,
            (song_id,),
        )
        changes = cursor.fetchall()

    assert [change["event_type"] for change in changes] == [
        "create",
        "song_replacement",
    ]
    assert [change["song_title"] for change in changes] == [
        "Old title",
        "New title",
    ]
    assert [change["changed_by"] for change in changes] == [2, 2]
    assert changes[-1]["changed_fields"]["notes"] == {
        "old": None,
        "new": "Updated notes",
    }

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type, changed_by, song_title, changed_fields
            FROM song_change
            WHERE song_country_id = 'US' AND song_year_id = 2025
            ORDER BY id
            """
        )
        deleted_changes = cursor.fetchall()

    assert [change["event_type"] for change in deleted_changes] == [
        "create",
        "song_modification",
        "delete",
    ]
    assert deleted_changes[1]["changed_fields"]["notes"] == {
        "old": None,
        "new": "Historical note",
    }
    assert deleted_changes[-1]["song_title"] == "Deleted song"
    assert all(change["changed_by"] == 2 for change in deleted_changes)


def test_changes_page_uses_timestamp_cursor(client, db):
    song_id = _add_song(db, title="Revision 0")
    _login_admin(client, db)

    revision_ids = []
    with db.cursor() as cursor:
        cursor.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,))
        revision_ids.append(cursor.fetchone()["song_data_id"])
        for revision in range(1, 27):
            row = create_song_revision(
                cursor,
                song_id,
                {"title": f"Revision {revision}"},
                changed_by=2,
            )
            revision_ids.append(row["id"])
        for revision, revision_id in enumerate(revision_ids):
            cursor.execute(
                """
                UPDATE song_data
                SET created_at = TIMESTAMPTZ '2025-01-01 00:00:00+00'
                               + %s * INTERVAL '1 second'
                WHERE id = %s
                """,
                (revision, revision_id),
            )
        cursor.execute(
            "SELECT created_at FROM song_data WHERE id = %s",
            (revision_ids[2],),
        )
        second_page_before = cursor.fetchone()["created_at"].isoformat()
    db.commit()

    first_page, first_context = _get_changes(client)

    assert first_page.status_code == 200
    assert len(first_context["changes"]) == 25
    assert first_context["changes"][0]["song_title"] == "Revision 26"
    assert first_context["changes"][-1]["song_title"] == "Revision 2"
    assert first_context["next_before"] is not None

    second_page, second_context = _get_changes(client, before=second_page_before)

    assert second_page.status_code == 200
    assert [change["song_title"] for change in second_context["changes"]] == [
        "Revision 1",
        "Revision 0",
    ]

    timespan, timespan_context = _get_changes(
        client,
        from_time="2025-01-01T00:00:10",
        to_time="2025-01-01T00:00:12",
    )

    assert timespan.status_code == 200
    assert [change["song_title"] for change in timespan_context["changes"]] == [
        "Revision 12",
        "Revision 11",
        "Revision 10",
    ]

    from_time_only, from_context = _get_changes(client, from_time="2025-01-01T00:00:10")
    to_time_only, to_context = _get_changes(client, to_time="2025-01-01T00:00:12")

    assert from_time_only.status_code == 200
    assert len(from_context["changes"]) == 17
    assert to_time_only.status_code == 200
    assert len(to_context["changes"]) == 13


def test_changes_page_keeps_boundary_timestamp_together(client, db):
    song_id = _add_song(db, title="Revision 0")
    _login_admin(client, db)
    with db.cursor() as cursor:
        cursor.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,))
        revision_ids = [cursor.fetchone()["song_data_id"]]
        for revision in range(1, 27):
            row = create_song_revision(
                cursor,
                song_id,
                {"title": f"Revision {revision}"},
                changed_by=2,
            )
            revision_ids.append(row["id"])
        for revision, revision_id in enumerate(revision_ids):
            timestamp_offset = max(revision, 2)
            cursor.execute(
                """
                UPDATE song_data
                SET created_at = TIMESTAMPTZ '2025-01-01 00:00:00+00'
                               + %s * INTERVAL '1 second'
                WHERE id = %s
                """,
                (timestamp_offset, revision_id),
            )
    db.commit()

    response, context = _get_changes(client)

    assert response.status_code == 200
    assert len(context["changes"]) == 27
    assert context["next_before"] is None


def test_changes_page_keeps_cross_table_boundary_timestamp_together(
    app, client, db
):
    song_id = _add_song(db, title="Revision 0")
    _login_admin(client, db)
    app.config["PERFORMANCE_HEADERS"] = True

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT song_data_id FROM current_song WHERE id = %s",
            (song_id,),
        )
        revision_ids = [cursor.fetchone()["song_data_id"]]
        for revision in range(1, 26):
            row = create_song_revision(
                cursor,
                song_id,
                {"title": f"Revision {revision}"},
                changed_by=2,
            )
            revision_ids.append(row["id"])

        cursor.execute(
            """
            UPDATE song_data
            SET created_at = CASE
                WHEN id = %s THEN TIMESTAMPTZ '2025-01-01 00:00:00+00'
                WHEN id = %s THEN TIMESTAMPTZ '2025-01-01 00:00:05+00'
                ELSE TIMESTAMPTZ '2025-01-01 00:00:10+00'
                     + array_position(%s::bigint[], id) * INTERVAL '1 second'
            END
            WHERE id = ANY(%s)
            """,
            (
                revision_ids[0],
                revision_ids[1],
                revision_ids,
                revision_ids,
            ),
        )
        cursor.execute(
            """
            UPDATE song_status
            SET created_at = TIMESTAMPTZ '2025-01-01 00:00:00+00'
            WHERE song_id = %s
            """,
            (song_id,),
        )
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=True)
        cursor.execute(
            """
            UPDATE song_status
            SET created_at = TIMESTAMPTZ '2025-01-01 00:00:05+00'
            WHERE song_id = %s AND is_placeholder
            """,
            (song_id,),
        )
    db.commit()

    response, context = _get_changes(client)

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "3"
    assert len(context["changes"]) == 26
    boundary = context["changes"][-1]["changed_at"]
    assert boundary == datetime.fromisoformat("2025-01-01T00:00:05+00:00")
    assert sum(change["changed_at"] == boundary for change in context["changes"]) == 2
    assert {change["source"] for change in context["changes"][-2:]} == {"d", "s"}
    assert context["next_before"] == boundary.isoformat()


def test_changes_page_includes_verification_decisions(client, db):
    song_id = _add_song(db)
    _login_admin(client, db)
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT song_data_id FROM current_song WHERE id = %s",
            (song_id,),
        )
        song_data_id = cursor.fetchone()["song_data_id"]
        cursor.execute(
            """
            INSERT INTO song_status (
                song_id, song_data_id, approval_status, is_placeholder,
                changed_by, created_at
            ) VALUES
                (%s, %s, 'accepted', false, 1,
                 CURRENT_TIMESTAMP + INTERVAL '1 second'),
                (%s, %s, 'more-info', false, 1,
                 CURRENT_TIMESTAMP + INTERVAL '2 seconds'),
                (%s, %s, 'rejected', false, 1,
                 CURRENT_TIMESTAMP + INTERVAL '3 seconds'),
                (%s, %s, 'pending', false, 1,
                 CURRENT_TIMESTAMP + INTERVAL '4 seconds'),
                (%s, %s, 'accepted', false, NULL,
                 CURRENT_TIMESTAMP + INTERVAL '5 seconds')
            """,
            (
                song_id,
                song_data_id,
                song_id,
                song_data_id,
                song_id,
                song_data_id,
                song_id,
                song_data_id,
                song_id,
                song_data_id,
            ),
        )
    db.commit()

    response, context = _get_changes(client, events="status_change")

    assert response.status_code == 200
    assert [change["change_details"] for change in context["changes"]] == [
        ["approval_status: rejected → pending"],
        ["approval_status: more-info → rejected"],
        ["approval_status: accepted → more-info"],
        ["approval_status: pending → accepted"],
    ]


def test_content_revision_can_have_multiple_categories_without_status(client, db):
    song_id = _add_song(db)
    _login_admin(client, db)
    with db.cursor() as cursor:
        create_song_revision(
            cursor,
            song_id,
            {
                "title": "Replacement title",
                "notes": "A simultaneous note",
                "sources": "A simultaneous source",
            },
            changed_by=2,
        )
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=True)
    db.commit()

    response, context = _get_changes(
        client,
        events=["replacement", "modification", "placeholder"],
    )

    assert response.status_code == 200
    changes = {
        tuple(change["event_categories"]): change["change_details"]
        for change in context["changes"]
    }
    assert changes == {
        ("replacement",): [
            "Title: Original title → Replacement title",
            "notes, sources",
        ],
        ("placeholder",): ["is_placeholder: false → true"],
    }


def test_typed_field_filters_support_boundaries_and_multiple_conditions(client, db):
    song_id = _add_song(db)
    _login_admin(client, db)
    with db.cursor() as cursor:
        create_song_revision(cursor, song_id, {"snippet_start": 10}, changed_by=2)
        create_song_revision(
            cursor,
            song_id,
            {"snippet_end": 20, "notes": "Changed too"},
            changed_by=2,
        )
        create_song_revision(
            cursor,
            song_id,
            {"translated_lyrics": "Some lyrics"},
            changed_by=2,
        )
        create_song_revision(cursor, song_id, {"snippet_start": 15}, changed_by=2)
        create_song_revision(cursor, song_id, {"native_title": "Été"}, changed_by=2)
    db.commit()

    numeric_value, numeric_value_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps([{"field": "snippet_start", "from": 10, "to": 15}]),
    )
    numeric_comparison, numeric_comparison_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "snippet_start",
                    "from": 10,
                    "from_operator": "gte",
                    "to": 20,
                    "to_operator": "lt",
                }
            ]
        ),
    )
    excluded_numeric_comparison, excluded_numeric_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "snippet_start",
                    "from": 10,
                    "from_operator": "gt",
                }
            ]
        ),
    )
    multiple_changes, multiple_changes_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {"field": "snippet_end"},
                {"field": "notes"},
            ]
        ),
    )
    text_value, text_value_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "translated_lyrics",
                    "to": "Some lyric_",
                }
            ]
        ),
    )
    text_operator_responses = []
    for operator, value in (
        ("starts_with", "Some"),
        ("ends_with", "lyrics"),
        ("contains", "me lyr"),
    ):
        text_operator_responses.append(
            _get_changes(
                client,
                events="modification",
                filters=json.dumps(
                    [
                        {
                            "field": "translated_lyrics",
                            "to": value,
                            "to_operator": operator,
                        }
                    ]
                ),
            )
        )
    case_insensitive, case_insensitive_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "translated_lyrics",
                    "to": "some",
                    "to_operator": "starts_with",
                    "to_case_sensitive": False,
                }
            ]
        ),
    )
    case_sensitive, case_sensitive_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "translated_lyrics",
                    "to": "some",
                    "to_operator": "starts_with",
                    "to_case_sensitive": True,
                }
            ]
        ),
    )
    default_text_normalization, default_text_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "native_title",
                    "to": "ete",
                }
            ]
        ),
    )
    accent_sensitive, accent_sensitive_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {
                    "field": "native_title",
                    "to": "ete",
                    "to_case_sensitive": False,
                    "to_accent_sensitive": True,
                }
            ]
        ),
    )
    disjunction, disjunction_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {"field": "snippet_end"},
                {"field": "translated_lyrics", "join": "or", "to": "Some %"},
            ]
        ),
    )
    entry_identity, entry_identity_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps(
            [
                {"field": "country_id", "to": "ES"},
                {"field": "year_id", "join": "and", "to": 2025},
            ]
        ),
    )
    other_country, other_country_context = _get_changes(
        client,
        events="modification",
        filters=json.dumps([{"field": "country_id", "to": "FR"}]),
    )

    assert numeric_value.status_code == 200
    assert [change["change_details"] for change in numeric_value_context["changes"]] == [
        ["snippet_start"]
    ]
    assert numeric_comparison.status_code == 200
    assert [change["change_details"] for change in numeric_comparison_context["changes"]] == [
        ["snippet_start"]
    ]
    assert excluded_numeric_comparison.status_code == 200
    assert excluded_numeric_context["changes"] == []
    assert multiple_changes.status_code == 200
    assert [change["change_details"] for change in multiple_changes_context["changes"]] == [
        ["notes, snippet_end"]
    ]
    assert text_value.status_code == 200
    assert [change["change_details"] for change in text_value_context["changes"]] == [
        ["translated_lyrics"]
    ]
    assert all(response.status_code == 200 for response, _ in text_operator_responses)
    assert all(
        [change["change_details"] for change in context["changes"]] == [["translated_lyrics"]]
        for _, context in text_operator_responses
    )
    assert case_insensitive.status_code == 200
    assert len(case_insensitive_context["changes"]) == 1
    assert case_sensitive.status_code == 200
    assert case_sensitive_context["changes"] == []
    assert default_text_normalization.status_code == 200
    assert len(default_text_context["changes"]) == 1
    assert accent_sensitive.status_code == 200
    assert accent_sensitive_context["changes"] == []
    assert disjunction.status_code == 200
    assert {tuple(change["change_details"]) for change in disjunction_context["changes"]} == {
        ("notes, snippet_end",),
        ("translated_lyrics",),
    }
    assert entry_identity.status_code == 200
    assert len(entry_identity_context["changes"]) == 5
    assert other_country.status_code == 200
    assert other_country_context["changes"] == []


def test_specific_outcome_filters(client, db):
    song_id = _add_song(db)
    _login_admin(client, db)
    with db.cursor() as cursor:
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=True)
        set_song_status(cursor, song_id, changed_by=2, is_placeholder=False)
        cursor.execute("SELECT song_data_id FROM current_song WHERE id = %s", (song_id,))
        song_data_id = cursor.fetchone()["song_data_id"]
        cursor.execute(
            """
            INSERT INTO song_status (
                song_id, song_data_id, approval_status, is_placeholder, changed_by
            ) VALUES
                (%s, %s, 'accepted', false, 1),
                (%s, %s, 'rejected', false, 1)
            """,
            (song_id, song_data_id, song_id, song_data_id),
        )
    db.commit()

    placeholder, placeholder_context = _get_changes(
        client,
        events="placeholder",
        filters=json.dumps([{"field": "is_placeholder", "from": False, "to": True}]),
    )
    rejected, rejected_context = _get_changes(
        client,
        events="status_change",
        filters=json.dumps(
            [
                {
                    "field": "approval_status",
                    "from": "accepted",
                    "to": "rejected",
                }
            ]
        ),
    )
    not_rejected, not_rejected_context = _get_changes(
        client,
        events="status_change",
        filters=json.dumps(
            [
                {
                    "field": "approval_status",
                    "to": "rejected",
                    "not": True,
                }
            ]
        ),
    )

    assert placeholder.status_code == 200
    assert [change["change_details"] for change in placeholder_context["changes"]] == [
        ["is_placeholder: false → true"]
    ]
    assert rejected.status_code == 200
    assert [change["change_details"] for change in rejected_context["changes"]] == [
        ["approval_status: accepted → rejected"]
    ]
    assert not_rejected.status_code == 200
    assert [change["change_details"] for change in not_rejected_context["changes"]] == [
        ["approval_status: pending → accepted"]
    ]
