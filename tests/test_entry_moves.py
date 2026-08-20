import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.song_revisions import create_song_revision, set_song_status


@pytest.fixture()
def move_years(db):
    db.execute(
        """INSERT INTO year (id, status, submissions_open, host_id)
           VALUES (2026, 'open', true, 'US'), (2027, 'ongoing', false, 'FR')
           ON CONFLICT (id) DO UPDATE
           SET status = EXCLUDED.status,
               submissions_open = EXCLUDED.submissions_open"""
    )
    db.commit()


def _add_song(db, country, year, submitter, *, placeholder=False, title="Song"):
    with db.cursor() as cursor:
        row = cursor.execute(
            """WITH inserted AS (
                   INSERT INTO song (country_id, year_id, entry_number)
                   VALUES (%s, %s, 1) RETURNING id
               )
               INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id
               )
               SELECT id, %s, %s, test_artist_credit('Artist') FROM inserted
               RETURNING song_id, id""",
            (country, year, submitter, title),
        ).fetchone()
        set_song_status(
            cursor,
            row["song_id"],
            changed_by=submitter,
            is_placeholder=placeholder,
        )
    db.commit()
    return row


def _remove_songs(db, song_ids):
    db.rollback()
    db.execute(
        """DELETE FROM song_verification_comment
           WHERE song_data_id IN (
               SELECT id FROM song_data WHERE song_id = ANY(%s)
           )""",
        (song_ids,),
    )
    db.execute("DELETE FROM song_status WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song_data WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song WHERE id = ANY(%s)", (song_ids,))
    db.commit()


def test_move_discovery_exposes_only_owned_entries_and_usable_destinations(
    client, db, move_years, login
):
    own = _add_song(db, "ES", 2025, 2)
    other = _add_song(db, "US", 2025, 3)
    occupied = _add_song(db, "US", 2026, 3)
    placeholder = _add_song(db, "FR", 2026, 3, placeholder=True)
    login(2)

    @given(year=st.sampled_from([2025, 2026, 2027]))
    def property_test(year):
        entries = client.get(f"/member/move/{year}")
        assert entries.status_code == 200
        returned_ids = {entry["id"] for entry in entries.get_json()["entries"]}
        assert returned_ids == ({own["song_id"]} if year == 2025 else set())

        destinations = client.get(f"/member/move/destinations/{year}?song_id={own['song_id']}")
        if year == 2027:
            assert destinations.status_code == 400
            return

        assert destinations.status_code == 200
        countries = {
            country["cc"]: country["replaces_placeholder"]
            for country in destinations.get_json()["countries"]
        }
        assert "US" not in countries
        assert ("ES" in countries) is (year == 2026)
        assert countries.get("FR") is (year == 2026)

    try:
        property_test()
    finally:
        _remove_songs(
            db,
            [
                own["song_id"],
                other["song_id"],
                occupied["song_id"],
                placeholder["song_id"],
            ],
        )


def test_moving_an_owned_entry_preserves_identity_history_and_comments(
    client, db, move_years, login
):
    login(2)

    @settings(max_examples=8, deadline=None)
    @given(
        destination=st.sampled_from(["US", "FR"]),
        has_placeholder=st.booleans(),
        comment_count=st.integers(min_value=0, max_value=3),
    )
    def property_test(destination, has_placeholder, comment_count):
        source = _add_song(db, "ES", 2025, 2)
        song_ids = [source["song_id"]]
        if has_placeholder:
            placeholder = _add_song(db, destination, 2026, 3, placeholder=True, title="Reserved")
            song_ids.append(placeholder["song_id"])

        with db.cursor() as cursor:
            revision_ids = [source["id"]]
            if comment_count > 1:
                revision = create_song_revision(
                    cursor, source["song_id"], {"notes": "More detail"}, changed_by=2
                )
                revision_ids.append(revision["id"])
            for index in range(comment_count):
                cursor.execute(
                    """INSERT INTO song_verification_comment (
                           song_data_id, author_id, body
                       ) VALUES (%s, 1, %s)""",
                    (revision_ids[index % len(revision_ids)], f"Check {index}"),
                )
        db.commit()

        try:
            response = client.post(
                "/member/move",
                json={
                    "song_id": source["song_id"],
                    "to_year": 2026,
                    "to_country": destination,
                },
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 200

            current = db.execute(
                "SELECT id, year_id, country_id FROM current_song WHERE id = %s",
                (source["song_id"],),
            ).fetchone()
            assert current == {
                "id": source["song_id"],
                "year_id": 2026,
                "country_id": destination,
            }
            comments = db.execute(
                """SELECT COUNT(*) AS count,
                          BOOL_AND(data.song_id = %s) AS follow_source
                   FROM song_verification_comment AS comment
                   JOIN song_data AS data ON data.id = comment.song_data_id
                   WHERE data.song_id = %s""",
                (source["song_id"], source["song_id"]),
            ).fetchone()
            assert comments["count"] == comment_count
            assert comments["follow_source"] is (True if comment_count else None)
            if has_placeholder:
                assert (
                    db.execute(
                        "SELECT 1 FROM current_song WHERE id = %s",
                        (placeholder["song_id"],),
                    ).fetchone()
                    is None
                )
        finally:
            _remove_songs(db, song_ids)

    property_test()


def test_invalid_moves_leave_the_entry_in_its_original_slot(client, db, move_years, login):
    login(2)

    @settings(max_examples=8, deadline=None)
    @given(reason=st.sampled_from(["not-owner", "closed-source", "closed-target", "occupied"]))
    def property_test(reason):
        source_year = 2024 if reason == "closed-source" else 2025
        owner = 3 if reason == "not-owner" else 2
        source = _add_song(db, "ES", source_year, owner)
        song_ids = [source["song_id"]]
        target_year = 2027 if reason == "closed-target" else 2026
        target_country = "US"
        if reason == "occupied":
            occupant = _add_song(db, target_country, target_year, 3)
            song_ids.append(occupant["song_id"])

        try:
            response = client.post(
                "/member/move",
                json={
                    "song_id": source["song_id"],
                    "to_year": target_year,
                    "to_country": target_country,
                },
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 400
            current = db.execute(
                "SELECT year_id, country_id FROM current_song WHERE id = %s",
                (source["song_id"],),
            ).fetchone()
            assert current == {"year_id": source_year, "country_id": "ES"}
        finally:
            _remove_songs(db, song_ids)

    property_test()
