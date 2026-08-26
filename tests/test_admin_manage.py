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
        assert stored == value.replace(second=0, microsecond=0, tzinfo=UTC)

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
