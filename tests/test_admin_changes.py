import string

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from world_stage.utils.song_revisions import create_song_revision, withdraw_song


def _add_song(
    db,
    *,
    title="Original title",
    artist="Original artist",
    country="ES",
    submitter=2,
):
    db.rollback()
    with db.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_user_id', '2', false)")
        song_id = cursor.execute(
            """WITH inserted AS (
                   INSERT INTO song (country_id, year_id, entry_number)
                   SELECT %s, 2025, COALESCE(MAX(entry_number), 0) + 1
                   FROM song WHERE country_id = %s AND year_id = 2025
                   RETURNING id
               )
               INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id
               )
               SELECT id, %s, %s, test_artist_credit(%s) FROM inserted
               RETURNING song_id""",
            (country, country, submitter, title, artist),
        ).fetchone()["song_id"]
    db.commit()
    return song_id


def _changes(db, song_id):
    return db.execute(
        """SELECT event_type, changed_by, changed_fields
           FROM song_change WHERE song_id = %s ORDER BY id""",
        (song_id,),
    ).fetchall()


def test_revision_events_classify_the_semantic_kind_of_change(db):
    value = st.text(alphabet=string.ascii_letters + " -_", min_size=1, max_size=30)

    @settings(max_examples=12, deadline=None)
    @given(
        operation=st.sampled_from(["metadata", "replacement", "ownership", "delete"]),
        generated=value,
    )
    def property_test(operation, generated):
        assume(operation != "replacement" or generated != "Original title")
        song_id = _add_song(db)
        with db.cursor() as cursor:
            if operation == "metadata":
                create_song_revision(cursor, song_id, {"notes": generated}, changed_by=2)
            elif operation == "replacement":
                create_song_revision(cursor, song_id, {"title": generated}, changed_by=2)
            elif operation == "ownership":
                create_song_revision(cursor, song_id, {"submitter_id": 3}, changed_by=2)
            else:
                withdraw_song(cursor, song_id, changed_by=2)
        db.commit()

        changes = _changes(db, song_id)
        assert [change["event_type"] for change in changes] == [
            "create",
            {
                "metadata": "song_modification",
                "replacement": "song_replacement",
                "ownership": "ownership_change",
                "delete": "delete",
            }[operation],
        ]
        assert all(change["changed_by"] == 2 for change in changes)
        if operation in {"metadata", "replacement"}:
            field = "notes" if operation == "metadata" else "title"
            assert changes[-1]["changed_fields"][field]["new"] == generated

    property_test()


def test_revisions_preserve_entry_identity_at_the_time_they_were_created(db):
    title = st.text(alphabet=string.ascii_letters + " -_", min_size=1, max_size=30)

    @settings(max_examples=12, deadline=None)
    @given(
        destination=st.sampled_from(["US", "FR"]),
        original_title=title,
        replacement_title=title,
    )
    def property_test(destination, original_title, replacement_title):
        assume(original_title != replacement_title)
        song_id = _add_song(db, title=original_title)
        with db.cursor() as cursor:
            cursor.execute(
                """UPDATE song
                   SET country_id = %s,
                       entry_number = (
                           SELECT COALESCE(MAX(entry_number), 0) + 1
                           FROM song
                           WHERE country_id = %s AND year_id = 2025
                       )
                   WHERE id = %s""",
                (destination, destination, song_id),
            )
            create_song_revision(
                cursor,
                song_id,
                {"title": replacement_title},
                changed_by=2,
            )
        db.commit()

        revisions = db.execute(
            """SELECT country_id, title FROM song_data
               WHERE song_id = %s ORDER BY created_at, id""",
            (song_id,),
        ).fetchall()
        assert revisions == [
            {"country_id": "ES", "title": original_title},
            {"country_id": destination, "title": replacement_title},
        ]
        assert db.execute(
            "SELECT country_id, title FROM current_song WHERE id = %s", (song_id,)
        ).fetchone() == {
            "country_id": destination,
            "title": replacement_title,
        }

    property_test()


def test_restoring_required_identity_after_withdrawal_starts_a_new_lifecycle(db):
    @settings(max_examples=12, deadline=None)
    @given(
        title=st.text(alphabet=string.ascii_letters + " -_", min_size=1, max_size=30),
        use_helper=st.booleans(),
    )
    def property_test(title, use_helper):
        song_id = _add_song(db)
        with db.cursor() as cursor:
            if use_helper:
                withdraw_song(cursor, song_id, changed_by=2)
            else:
                cursor.execute(
                    """INSERT INTO song_data (
                           song_id, title, artist_credit_set_id, changed_by
                       ) VALUES (%s, NULL, NULL, 2)""",
                    (song_id,),
                )
            cursor.execute(
                """INSERT INTO song_data (
                       song_id, title, artist_credit_set_id, changed_by
                   ) VALUES (%s, %s, test_artist_credit('Returned artist'), 2)""",
                (song_id, title),
            )
        db.commit()

        assert [change["event_type"] for change in _changes(db, song_id)] == [
            "create",
            "delete",
            "create",
        ]
        assert (
            db.execute("SELECT title FROM current_song WHERE id = %s", (song_id,)).fetchone()[
                "title"
            ]
            == title
        )

    property_test()
