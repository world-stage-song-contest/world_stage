import uuid

import pytest


@pytest.fixture()
def admin_session(client, db):
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

    yield

    db.rollback()
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        cursor.execute(
            """UPDATE year
               SET host_id = 'US', status = 'open', submissions_open = true,
                   scoreboard_style = 'esc-1997'
               WHERE id = 2025"""
        )
    db.commit()


@pytest.fixture()
def final_and_spanish_entry(db):
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """
            INSERT INTO show (year_id, show_type, status)
            VALUES (2025, 'f', 'none')
            RETURNING id
            """
        )
        final_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO song (country_id, year_id, entry_number)
            VALUES ('ES', 2025, 1)
            RETURNING id
            """
        )
        song_id = cursor.fetchone()["id"]
    db.commit()

    yield final_id, song_id

    with db.cursor() as cursor:
        cursor.execute("DELETE FROM song_show WHERE show_id = %s", (final_id,))
        cursor.execute("DELETE FROM song WHERE id = %s", (song_id,))
        cursor.execute("DELETE FROM show WHERE id = %s", (final_id,))
    db.commit()


def test_manage_year_can_set_host(client, db, admin_session, final_and_spanish_entry):
    final_id, song_id = final_and_spanish_entry
    response = client.post(
        "/admin/manage/2025",
        data={"action": "set_host", "host_id": "ES"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/admin/manage/2025")
    with db.cursor() as cursor:
        cursor.execute("SELECT host_id FROM year WHERE id = 2025")
        assert cursor.fetchone()["host_id"] == "ES"
        cursor.execute(
            """
            SELECT song_id, running_order
            FROM song_show
            WHERE show_id = %s
            """,
            (final_id,),
        )
        assert cursor.fetchone() == {
            "song_id": song_id,
            "running_order": 1,
        }


def test_lineup_issues_only_block_relevant_state_transitions(
    client, db, admin_session, final_and_spanish_entry
):
    final_id, _ = final_and_spanish_entry

    page = client.get("/admin/manage/2025", headers={"Accept": "application/json"})
    assert page.status_code == 200
    data = page.get_json()
    assert "lineup_issues" not in data
    assert "unassigned_lineup_issue" not in data

    response = client.post(
        "/admin/manage/2025/f",
        json={"action": "open_voting"},
    )
    assert response.status_code == 400
    response_codes = {
        issue["code"] for issue in response.get_json()["lineup_issues"]
    }
    assert "lineup_empty" in response_codes
    assert "main_participant_unassigned" not in response_codes

    response = client.post(
        "/admin/manage/2025/f",
        json={"action": "set_status", "status": "full"},
    )
    assert response.status_code == 400
    assert "lineup_empty" in {
        issue["code"] for issue in response.get_json()["lineup_issues"]
    }

    response = client.post(
        "/admin/manage/2025",
        json={"action": "change_year_status", "year_status": "ongoing"},
    )
    assert response.status_code == 400
    assert response.get_json()["lineup_issues"] == [
        {
            "code": "main_participant_unassigned",
            "message": "1 main contest participant is not assigned to any show.",
        }
    ]
    with db.cursor() as cursor:
        cursor.execute("SELECT voting_opens FROM show WHERE id = %s", (final_id,))
        assert cursor.fetchone()["voting_opens"] is None


def test_manage_year_rejects_unknown_host(client, db, admin_session):
    response = client.post(
        "/admin/manage/2025",
        data={"action": "set_host", "host_id": "ZZ"},
    )

    assert response.status_code == 400
    with db.cursor() as cursor:
        cursor.execute("SELECT host_id FROM year WHERE id = 2025")
        assert cursor.fetchone()["host_id"] == "US"


def test_manage_year_can_clear_host(client, db, admin_session):
    response = client.post(
        "/admin/manage/2025",
        data={"action": "set_host", "host_id": ""},
    )

    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute("SELECT host_id FROM year WHERE id = 2025")
        assert cursor.fetchone()["host_id"] is None


def test_manage_year_submission_status_is_independent_of_lifecycle(
    client, db, admin_session
):
    response = client.post(
        "/admin/manage/2025",
        json={"action": "set_submissions_open", "submissions_open": False},
    )

    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT status, submissions_open FROM year WHERE id = 2025"
        )
        assert cursor.fetchone() == {"status": "open", "submissions_open": False}

    response = client.post(
        "/admin/manage/2025",
        json={"action": "change_year_status", "year_status": "closed"},
    )
    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT status, submissions_open FROM year WHERE id = 2025"
        )
        assert cursor.fetchone() == {"status": "closed", "submissions_open": False}


def test_manage_year_can_change_scoreboard_style(client, db, admin_session):
    response = client.post(
        "/admin/manage/2025",
        json={"action": "set_scoreboard_style", "scoreboard_style": None},
    )

    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute("SELECT scoreboard_style FROM year WHERE id = 2025")
        assert cursor.fetchone()["scoreboard_style"] is None

    response = client.post(
        "/admin/manage/2025",
        json={"action": "set_scoreboard_style", "scoreboard_style": "esc-1997"},
    )

    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute("SELECT scoreboard_style FROM year WHERE id = 2025")
        assert cursor.fetchone()["scoreboard_style"] == "esc-1997"


def test_manage_year_rejects_unknown_scoreboard_style(client, db, admin_session):
    response = client.post(
        "/admin/manage/2025",
        json={"action": "set_scoreboard_style", "scoreboard_style": "unknown"},
    )

    assert response.status_code == 400
