import uuid

import pytest

from world_stage.utils.song_revisions import create_song_revision, set_song_status

HTML_HEADERS = {"Accept": "text/html"}


def _login(client, db, user_id: int):
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


@pytest.fixture()
def move_years(db):
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO year (id, status, submissions_open, host_id)
            VALUES (2026, 'open', true, 'US'), (2027, 'ongoing', false, 'FR')
            ON CONFLICT (id) DO UPDATE
            SET status = EXCLUDED.status,
                submissions_open = EXCLUDED.submissions_open
            """
        )
    db.commit()


def _add_song(db, country, year, submitter, *, placeholder=False, title="Song"):
    with db.cursor() as cursor:
        cursor.execute(
            """
            WITH inserted AS (
                INSERT INTO song (country_id, year_id, entry_number)
                VALUES (%s, %s, 1) RETURNING id
            )
            INSERT INTO song_data (song_id, submitter_id, title, artist)
            SELECT id, %s, %s, 'Artist' FROM inserted
            RETURNING song_id, id
            """,
            (country, year, submitter, title),
        )
        row = cursor.fetchone()
        set_song_status(
            cursor,
            row["song_id"],
            changed_by=submitter,
            is_placeholder=placeholder,
        )
    db.commit()
    return row


def test_user_page_lists_selected_year_and_moves_owned_entry(client, db, move_years):
    song = _add_song(db, "ES", 2025, 2)
    _login(client, db, 2)

    page = client.get("/member/move", headers=HTML_HEADERS)
    assert page.status_code == 200

    entries = client.get("/member/move/2025")
    assert entries.status_code == 200
    assert entries.json["entries"] == [
        {
            "id": song["song_id"],
            "cc": "ES",
            "country": "Spain",
        }
    ]

    destinations = client.get(f"/member/move/destinations/2026?song_id={song['song_id']}")
    assert destinations.status_code == 200
    assert {country["cc"] for country in destinations.json["countries"]} >= {
        "US",
        "ES",
        "FR",
    }

    response = client.post(
        "/member/move",
        json={
            "song_id": song["song_id"],
            "to_year": 2026,
            "to_country": "FR",
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.json["result"]["details_url"] == "/country/fr/2026"
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT current.id, current.year_id, current.country_id,
                   data.previous_revision_id, change.event_type
            FROM current_song AS current
            JOIN song_data AS data ON data.id = current.song_data_id
            JOIN song_change AS change ON change.id = data.id
            WHERE current.id = %s
            """,
            (song["song_id"],),
        )
        assert cursor.fetchone() == {
            "id": song["song_id"],
            "year_id": 2026,
            "country_id": "FR",
            "previous_revision_id": song["id"],
            "event_type": "song_modification",
        }


def test_move_replaces_placeholder_and_all_comments_follow(client, db, move_years):
    song = _add_song(db, "ES", 2025, 2)
    placeholder = _add_song(db, "FR", 2026, 3, placeholder=True, title="Reserved")
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO song_verification_comment (song_data_id, author_id, body)
               VALUES (%s, 1, 'First check')""",
            (song["id"],),
        )
        revision = create_song_revision(
            cursor, song["song_id"], {"notes": "More detail"}, changed_by=2
        )
        cursor.execute(
            """INSERT INTO song_verification_comment (song_data_id, author_id, body)
               VALUES (%s, 1, 'Second check')""",
            (revision["id"],),
        )
    db.commit()
    _login(client, db, 2)

    destinations = client.get(f"/member/move/destinations/2026?song_id={song['song_id']}")
    france = next(country for country in destinations.json["countries"] if country["cc"] == "FR")
    assert france["replaces_placeholder"] is True

    response = client.post(
        "/member/move",
        json={
            "song_id": song["song_id"],
            "to_year": 2026,
            "to_country": "FR",
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM current_song WHERE year_id = 2026 AND country_id = 'FR'")
        assert cursor.fetchone()["id"] == song["song_id"]
        cursor.execute("SELECT 1 FROM current_song WHERE id = %s", (placeholder["song_id"],))
        assert cursor.fetchone() is None
        cursor.execute(
            """
            SELECT data.year_id, data.country_id, COUNT(*) AS count
            FROM song_verification_comment AS comment
            JOIN song_data AS data ON data.id = comment.song_data_id
            GROUP BY data.year_id, data.country_id
            """
        )
        assert cursor.fetchall() == [{"year_id": 2026, "country_id": "FR", "count": 2}]


def test_user_cannot_move_someone_elses_entry_or_replace_real_slot(client, db, move_years):
    own = _add_song(db, "ES", 2025, 2)
    other = _add_song(db, "US", 2026, 3)
    _login(client, db, 2)

    destinations = client.get(f"/member/move/destinations/2026?song_id={own['song_id']}")
    assert "US" not in {country["cc"] for country in destinations.json["countries"]}

    occupied = client.post(
        "/member/move",
        data={
            "song_id": own["song_id"],
            "from_year": 2025,
            "to_year": 2026,
            "to_country": "US",
        },
        headers=HTML_HEADERS,
    )
    assert occupied.status_code == 400

    not_owned = client.post(
        "/member/move",
        data={
            "song_id": other["song_id"],
            "from_year": 2026,
            "to_year": 2025,
            "to_country": "FR",
        },
        headers=HTML_HEADERS,
    )
    assert not_owned.status_code == 400


def test_move_requires_open_source_and_destination(client, db, move_years):
    closed = _add_song(db, "ES", 2024, 2)
    upcoming = _add_song(db, "FR", 2025, 2)
    _login(client, db, 2)

    from_closed = client.post(
        "/member/move",
        data={
            "song_id": closed["song_id"],
            "from_year": 2024,
            "to_year": 2026,
            "to_country": "US",
        },
        headers=HTML_HEADERS,
    )
    assert from_closed.status_code == 400

    to_ongoing = client.post(
        "/member/move",
        data={
            "song_id": upcoming["song_id"],
            "from_year": 2025,
            "to_year": 2027,
            "to_country": "US",
        },
        headers=HTML_HEADERS,
    )
    assert to_ongoing.status_code == 400


def test_admin_move_page_uses_the_same_placeholder_rules(client, db, move_years):
    song = _add_song(db, "ES", 2025, 2)
    _add_song(db, "FR", 2026, 3, placeholder=True, title="Reserved")
    _login(client, db, 1)

    page = client.get("/admin/move", headers=HTML_HEADERS)
    assert page.status_code == 200

    response = client.post(
        "/admin/move",
        data={
            "from_year": 2025,
            "from_cc": "ES",
            "to_year": 2026,
            "to_cc": "FR",
        },
        headers=HTML_HEADERS,
    )
    assert response.status_code == 200
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM current_song WHERE year_id = 2026 AND country_id = 'FR'")
        assert cursor.fetchone()["id"] == song["song_id"]
