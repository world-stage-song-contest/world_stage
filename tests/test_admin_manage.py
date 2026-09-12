import importlib
import uuid
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st


@pytest.fixture()
def admin_session(client, db):
    session_id = uuid.uuid4()
    db.execute(
        """INSERT INTO session (user_id, session_id, expires_at)
           VALUES (1, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')""",
        (session_id,),
    )
    db.commit()
    client.set_cookie("session", str(session_id))
    yield
    client.delete_cookie("session")
    db.rollback()
    db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
    db.execute(
        """UPDATE year
           SET host_id = 'US', status = 'open', submissions_open = true,
               scoreboard_style = 'esc-1997'
           WHERE id = 2025"""
    )
    db.commit()


def test_host_selection_accepts_existing_countries_or_no_host(client, db, admin_session):
    db.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
    final_id = db.execute(
        """INSERT INTO show (year_id, show_type, status)
           VALUES (2025, 'f', 'none') RETURNING id"""
    ).fetchone()["id"]
    eligible_hosts = ["US", "ES", "FR"]
    song_ids = []
    for entry_number, country_id in enumerate(eligible_hosts, 1):
        song_ids.append(
            db.execute(
                """INSERT INTO song (country_id, year_id, entry_number)
                   VALUES (%s, 2025, %s) RETURNING id""",
                (country_id, entry_number),
            ).fetchone()["id"]
        )
    db.commit()
    cases = [*eligible_hosts, "", "ZZZ"]

    @given(host=st.sampled_from(cases))
    def property_test(host):
        db.execute("UPDATE year SET host_id = 'US' WHERE id = 2025")
        db.commit()

        response = client.post(
            "/admin/manage/2025",
            data={"action": "set_host", "host_id": host},
        )

        expected = None if host == "" else host if host in eligible_hosts else "US"
        assert response.status_code == (302 if host in eligible_hosts or host == "" else 400)
        assert (
            db.execute("SELECT host_id FROM year WHERE id = 2025").fetchone()["host_id"] == expected
        )

    try:
        property_test()
    finally:
        db.rollback()
        db.execute("DELETE FROM song_show WHERE show_id = %s", (final_id,))
        db.execute("DELETE FROM song WHERE id = ANY(%s)", (song_ids,))
        db.execute("DELETE FROM show WHERE id = %s", (final_id,))
        db.commit()


def test_submission_availability_is_independent_of_year_lifecycle(client, db, admin_session):
    @given(submissions_open=st.booleans(), lifecycle=st.sampled_from(["open", "closed"]))
    def property_test(submissions_open, lifecycle):
        db.execute("UPDATE year SET status = 'open', submissions_open = true WHERE id = 2025")
        db.commit()

        assert (
            client.post(
                "/admin/manage/2025",
                json={
                    "action": "set_submissions_open",
                    "submissions_open": submissions_open,
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/admin/manage/2025",
                json={"action": "change_year_status", "year_status": lifecycle},
            ).status_code
            == 200
        )

        assert db.execute(
            "SELECT status, submissions_open FROM year WHERE id = 2025"
        ).fetchone() == {
            "status": lifecycle,
            "submissions_open": submissions_open,
        }

    property_test()


def test_scoreboard_style_accepts_only_supported_values(client, db, admin_session):
    @given(style=st.sampled_from([None, "esc-1997", "unknown", "", "future-style"]))
    def property_test(style):
        db.execute("UPDATE year SET scoreboard_style = 'esc-1997' WHERE id = 2025")
        db.commit()

        response = client.post(
            "/admin/manage/2025",
            json={"action": "set_scoreboard_style", "scoreboard_style": style},
        )

        valid = style in {None, "esc-1997"}
        assert response.status_code == (200 if valid else 400)
        expected = style if valid else "esc-1997"
        assert (
            db.execute("SELECT scoreboard_style FROM year WHERE id = 2025").fetchone()[
                "scoreboard_style"
            ]
            == expected
        )

    property_test()


def test_show_start_form_values_are_stored_as_utc_instants(client, db, admin_session):
    db.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
    show_id = db.execute(
        """INSERT INTO show (year_id, show_type, status)
           VALUES (2025, 'f', 'none') RETURNING id"""
    ).fetchone()["id"]
    db.commit()

    @given(
        value=st.datetimes(
            min_value=datetime(1960, 1, 1),
            max_value=datetime(9999, 12, 31, 23, 59),
        )
    )
    def property_test(value):
        submitted = value.isoformat(timespec="minutes")

        response = client.post(
            "/admin/manage/2025/f",
            json={"action": "change_date", "date": submitted},
        )

        assert response.status_code == 200
        stored = db.execute("SELECT date FROM show WHERE id = %s", (show_id,)).fetchone()["date"]
        assert stored.astimezone(UTC) == value.replace(second=0, microsecond=0, tzinfo=UTC)

    try:
        property_test()
    finally:
        db.execute("DELETE FROM show WHERE id = %s", (show_id,))
        db.commit()


def test_lineup_issues_block_only_the_transition_they_make_unsafe(client, db, admin_session):
    db.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
    show_id = db.execute(
        """INSERT INTO show (year_id, show_type, status)
           VALUES (2025, 'f', 'none') RETURNING id"""
    ).fetchone()["id"]
    song_id = db.execute(
        """INSERT INTO song (country_id, year_id, entry_number)
           VALUES ('ES', 2025, 1) RETURNING id"""
    ).fetchone()["id"]
    db.commit()
    cases = [
        ("show", {"action": "open_voting"}, "lineup_empty"),
        ("show", {"action": "set_status", "status": "full"}, "lineup_empty"),
        (
            "year",
            {"action": "change_year_status", "year_status": "ongoing"},
            "main_participant_unassigned",
        ),
    ]

    @given(case=st.sampled_from(cases))
    def property_test(case):
        target, payload, expected_issue = case
        url = "/admin/manage/2025/f" if target == "show" else "/admin/manage/2025"

        response = client.post(url, json=payload)

        assert response.status_code == 400
        assert expected_issue in {issue["code"] for issue in response.get_json()["lineup_issues"]}

    try:
        property_test()
    finally:
        db.rollback()
        db.execute("DELETE FROM song_show WHERE show_id = %s", (show_id,))
        db.execute("DELETE FROM song WHERE id = %s", (song_id,))
        db.execute("DELETE FROM show WHERE id = %s", (show_id,))
        db.commit()


def test_discord_notification_follows_partial_publication_and_manual_retries(
    client, db, admin_session, monkeypatch
):
    manage_module = importlib.import_module("world_stage.routes.admin.manage")
    deliveries = []
    monkeypatch.setattr(manage_module, "get_lineup_issues", lambda cursor, show_id: [])
    monkeypatch.setattr(
        manage_module,
        "send_qualification_notification_best_effort",
        lambda show_id: deliveries.append(("automatic", show_id)),
    )
    monkeypatch.setattr(
        manage_module,
        "send_qualification_notification",
        lambda show_id: deliveries.append(("manual", show_id)),
    )

    @given(
        initial_status=st.sampled_from(["none", "draw", "partial", "full"]),
        manual=st.booleans(),
    )
    def property_test(initial_status, manual):
        db.execute(
            "INSERT INTO show_status (name) VALUES (%s) ON CONFLICT DO NOTHING",
            (initial_status,),
        )
        db.execute("INSERT INTO show_status (name) VALUES ('partial') ON CONFLICT DO NOTHING")
        stored_status = "partial" if manual else initial_status
        show_id = db.execute(
            """INSERT INTO show (year_id, show_type, show_number, status)
               VALUES (2025, 'sf', 91, %s) RETURNING id""",
            (stored_status,),
        ).fetchone()["id"]
        db.commit()
        before = len(deliveries)
        try:
            payload = (
                {"action": "send_discord_notification"}
                if manual
                else {"action": "set_status", "status": "partial"}
            )
            response = client.post("/admin/manage/2025/sf91", json=payload)

            assert response.status_code == 200
            expected = manual or initial_status != "partial"
            assert len(deliveries) - before == int(expected)
            if expected:
                assert deliveries[-1] == (
                    "manual" if manual else "automatic",
                    show_id,
                )
        finally:
            db.execute("DELETE FROM show WHERE id = %s", (show_id,))
            db.commit()

    property_test()


def test_running_order_notification_follows_voting_open_and_manual_retries(
    client, db, admin_session, monkeypatch
):
    manage_module = importlib.import_module("world_stage.routes.admin.manage")
    deliveries = []
    monkeypatch.setattr(manage_module, "get_lineup_issues", lambda cursor, show_id: [])
    monkeypatch.setattr(
        manage_module,
        "send_running_order_notification_best_effort",
        lambda show_id: deliveries.append(("automatic", show_id)),
    )
    monkeypatch.setattr(
        manage_module,
        "send_running_order_notification",
        lambda show_id: deliveries.append(("manual", show_id)),
    )

    @given(
        initial_state=st.sampled_from(["not_started", "open", "closed"]),
        manual=st.booleans(),
    )
    def property_test(initial_state, manual):
        stored_state = "open" if manual else initial_state
        show_id = db.execute(
            """INSERT INTO show (
                   year_id, show_type, show_number, status,
                   voting_opens, voting_closes
               ) VALUES (
                   2025, 'sf', 92, 'none',
                   CASE WHEN %s = 'not_started' THEN NULL ELSE CURRENT_TIMESTAMP END,
                   CASE WHEN %s = 'closed' THEN CURRENT_TIMESTAMP ELSE NULL END
               ) RETURNING id""",
            (stored_state, stored_state),
        ).fetchone()["id"]
        db.commit()
        before = len(deliveries)
        try:
            payload = (
                {"action": "send_running_order_notification"}
                if manual
                else {"action": "open_voting"}
            )
            response = client.post("/admin/manage/2025/sf92", json=payload)

            assert response.status_code == 200
            expected = manual or initial_state != "open"
            assert len(deliveries) - before == int(expected)
            if expected:
                assert deliveries[-1] == (
                    "manual" if manual else "automatic",
                    show_id,
                )
        finally:
            db.execute("DELETE FROM show WHERE id = %s", (show_id,))
            db.commit()

    property_test()


def test_final_results_notification_follows_full_reveal_and_manual_retries(
    client, db, admin_session, monkeypatch
):
    manage_module = importlib.import_module("world_stage.routes.admin.manage")
    deliveries = []
    monkeypatch.setattr(manage_module, "get_lineup_issues", lambda cursor, show_id: [])
    monkeypatch.setattr(
        manage_module,
        "send_final_results_notification_best_effort",
        lambda show_id: deliveries.append(("automatic", show_id)),
    )
    monkeypatch.setattr(
        manage_module,
        "send_final_results_notification",
        lambda show_id: deliveries.append(("manual", show_id)),
    )

    @given(
        initial_status=st.sampled_from(["none", "draw", "partial", "full"]),
        manual=st.booleans(),
    )
    def property_test(initial_status, manual):
        stored_status = "full" if manual else initial_status
        show_id = db.execute(
            """INSERT INTO show (year_id, show_type, status)
               VALUES (2025, 'f', %s) RETURNING id""",
            (stored_status,),
        ).fetchone()["id"]
        db.commit()
        before = len(deliveries)
        try:
            payload = (
                {"action": "send_final_results_notification"}
                if manual
                else {"action": "set_status", "status": "full"}
            )
            response = client.post("/admin/manage/2025/f", json=payload)

            assert response.status_code == 200
            expected = manual or initial_status != "full"
            assert len(deliveries) - before == int(expected)
            if expected:
                assert deliveries[-1] == (
                    "manual" if manual else "automatic",
                    show_id,
                )
        finally:
            db.execute("DELETE FROM show WHERE id = %s", (show_id,))
            db.commit()

    property_test()


def test_creating_country_national_final_keeps_existing_slot_song(
    client, db, admin_session, alice_headers
):
    @given(country=st.sampled_from(["ES", "FR", "US"]), has_song=st.booleans())
    def property_test(country, has_song):
        song_id = None
        if has_song:
            song_id = db.execute(
                """INSERT INTO song (country_id, year_id, entry_number)
                   VALUES (%s, 2025, 1) RETURNING id""",
                (country,),
            ).fetchone()["id"]
            db.execute(
                """INSERT INTO song_data (
                       song_id, submitter_id, title, artist_credit_set_id
                   ) VALUES (
                       %s, 2, 'Existing submission',
                       test_artist_credit('Existing artist')
                   )""",
                (song_id,),
            )
        db.commit()

        response = client.post(
            "/admin/manage/2025/create/nf",
            data={
                "owner_id": "2",
                "owner_country_id": country,
                "name": f"{country} National Final",
            },
        )

        assert response.status_code == 302
        national_final_id = db.execute(
            """SELECT id FROM national_final
               WHERE year_id = 2025 AND owner_country_id = %s""",
            (country,),
        ).fetchone()["id"]
        linked_song_ids = {
            row["song_id"]
            for row in db.execute(
                """SELECT song_id FROM national_final_song
                   WHERE national_final_id = %s""",
                (national_final_id,),
            ).fetchall()
        }
        assert linked_song_ids == ({song_id} if has_song else set())
        if song_id is None:
            candidate_response = client.post(
                "/api/song",
                headers=alice_headers,
                json={
                    "year": 2025,
                    "country": country,
                    "national_final_id": national_final_id,
                    "title": "First candidate",
                    "artist": "Candidate artist",
                    "languages": [20],
                },
            )
            assert candidate_response.status_code == 201
            song_id = candidate_response.get_json()["result"]["id"]

        if song_id is not None:
            assert db.execute(
                """SELECT song.main_participant,
                          national_final_song.national_final_id
                   FROM song
                   JOIN national_final_song
                     ON national_final_song.song_id = song.id
                   WHERE song.id = %s""",
                (song_id,),
            ).fetchone() == {
                "main_participant": False,
                "national_final_id": national_final_id,
            }
            assert db.execute(
                "SELECT entry_number FROM song WHERE id = %s", (song_id,)
            ).fetchone()["entry_number"] == 2

        db.execute(
            "DELETE FROM national_final_song WHERE national_final_id = %s",
            (national_final_id,),
        )
        db.execute("DELETE FROM national_final WHERE id = %s", (national_final_id,))
        if song_id is not None:
            db.execute("DELETE FROM song_status WHERE song_id = %s", (song_id,))
            db.execute("DELETE FROM song_data WHERE song_id = %s", (song_id,))
            db.execute("DELETE FROM song WHERE id = %s", (song_id,))
        db.commit()

    property_test()


def test_bulk_deletion_withdraws_only_placeholders_in_selected_year(client, db, login):
    from world_stage.utils.song_revisions import latest_song_data, set_song_status

    @given(
        entries=st.lists(
            st.tuples(st.sampled_from([2024, 2025]), st.booleans()), max_size=8
        ),
        target_year=st.sampled_from([2024, 2025]),
        actor=st.sampled_from([1, 2]),
    )
    def property_test(entries, target_year, actor):
        login(actor)
        songs = []
        try:
            for number, (year, placeholder) in enumerate(entries, 1):
                song_id = db.execute(
                    """INSERT INTO song (country_id, year_id, entry_number)
                       VALUES ('US', %s, %s) RETURNING id""",
                    (year, number),
                ).fetchone()["id"]
                db.execute(
                    """INSERT INTO song_data (song_id, submitter_id, title, artist_credit_set_id)
                       VALUES (%s, 2, 'Song', test_artist_credit('Artist'))""",
                    (song_id,),
                )
                set_song_status(db.cursor(), song_id, changed_by=2, is_placeholder=placeholder)
                songs.append((song_id, year, placeholder))
            db.commit()

            for _ in range(2):
                response = client.post(
                    f"/admin/manage/{target_year}", json={"action": "delete_placeholders"}
                )
                assert response.status_code == (200 if actor == 1 else 302)
                remaining = {
                    row["id"] for row in db.execute("SELECT id FROM current_song").fetchall()
                }
                expected = {
                    song_id for song_id, year, placeholder in songs
                    if not (actor == 1 and year == target_year and placeholder)
                }
                assert remaining == expected
                for song_id, _, _ in songs:
                    latest = latest_song_data(db.cursor(), song_id)
                    if song_id not in expected:
                        assert latest["title"] is None
                        assert latest["changed_by"] == actor
                    assert db.execute(
                        "SELECT 1 FROM song_data WHERE song_id = %s AND title = 'Song'",
                        (song_id,),
                    ).fetchone()
        finally:
            db.rollback()
            db.execute("DELETE FROM song_status")
            db.execute("DELETE FROM song_data")
            db.execute("DELETE FROM song")
            db.commit()

    property_test()
