import uuid

import psycopg
import pytest
from werkzeug.datastructures import MultiDict


def _set_session(client, db, user_id: int):
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


@pytest.fixture()
def national_final(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM point_system")
        point_system_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 3)",
            (point_system_id,),
        )
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM point")
        first_point_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO point (id, point_system_id, place, score) VALUES (%s, %s, %s, %s)",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
                (first_point_id + 2, point_system_id, 3, 8),
            ],
        )
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM national_final")
        national_final_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO national_final (
                id, year_id, owner_id, owner_country_id, short_name, name
            ) VALUES (%s, 2025, 2, 'ES', 'test-es', 'Test Spanish Final')
            """,
            (national_final_id,),
        )
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM show")
        show_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO show (
                id, year_id, point_system_id, show_type, status,
                national_final_id
            ) VALUES (%s, 2025, %s, 'f', 'none', %s)
            """,
            (show_id, point_system_id, national_final_id),
        )
    db.commit()

    yield {
        "id": national_final_id,
        "show_id": show_id,
        "point_system_id": point_system_id,
    }

    db.rollback()
    with db.cursor() as cursor:
        cursor.execute(
            "DELETE FROM vote WHERE vote_set_id IN (SELECT id FROM vote_set WHERE show_id = %s)",
            (show_id,),
        )
        cursor.execute("DELETE FROM vote_set WHERE show_id = %s", (show_id,))
        cursor.execute("DELETE FROM song_show WHERE show_id = %s", (show_id,))
        cursor.execute(
            "DELETE FROM national_final_song WHERE national_final_id = %s",
            (national_final_id,),
        )
        cursor.execute("DELETE FROM show WHERE id = %s", (show_id,))
        cursor.execute("DELETE FROM national_final WHERE id = %s", (national_final_id,))
        cursor.execute("DELETE FROM point WHERE point_system_id = %s", (point_system_id,))
        cursor.execute("DELETE FROM point_system WHERE id = %s", (point_system_id,))
    db.commit()


def test_composite_show_key_is_discovered(client, national_final):
    response = client.get("/api/show?year=2025")
    assert response.status_code == 200
    shows = response.get_json()["result"]
    show = next(item for item in shows if item["id"] == national_final["show_id"])
    assert show["key"] == "2025-test-es-f"
    assert show["short_name"] == "test-es-f"
    assert show["local_short_name"] == "f"


def test_national_final_shows_use_type_and_number_order(client, db, national_final):
    show_ids = []
    try:
        with db.cursor() as cursor:
            for show_type, show_number in (("sc", None), ("sf", 2), ("sf", 1)):
                cursor.execute(
                    """
                    INSERT INTO show (
                        year_id, point_system_id, show_type, show_number,
                        status, national_final_id
                    ) VALUES (2025, %s, %s, %s, 'none', %s)
                    RETURNING id
                    """,
                    (
                        national_final["point_system_id"],
                        show_type,
                        show_number,
                        national_final["id"],
                    ),
                )
                show_ids.append(cursor.fetchone()["id"])
        db.commit()

        response = client.get(
            "/year/2025/nfs/test-es", headers={"Accept": "application/json"}
        )

        assert response.status_code == 200
        assert [show["short_name"] for show in response.get_json()["shows"]] == [
            "sf1",
            "sf2",
            "sc",
            "f",
        ]
    finally:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM show WHERE id = ANY(%s)", (show_ids,))
        db.commit()


def test_show_progression_uses_direct_show_references(client, db, national_final):
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_type, show_number, status,
                national_final_id
            ) VALUES (2025, %s, 'sf', 77, 'none', %s)
            RETURNING id
            """,
            (national_final["point_system_id"], national_final["id"]),
        )
        source_show_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO show_progression (
                source_show_id, target_show_id, qualifier_count, priority
            ) VALUES (%s, %s, 5, 1)
            """,
            (source_show_id, national_final["show_id"]),
        )
        cursor.execute(
            "SELECT show_progression_status(%s, place) AS status "
            "FROM generate_series(1, 6) AS place ORDER BY place",
            (source_show_id,),
        )
        assert [row["status"] for row in cursor.fetchall()] == [
            "f", "f", "f", "f", "f", "nq"
        ]
    db.commit()

    try:
        response = client.get("/api/show?year=2025")
        show = next(
            item for item in response.get_json()["result"] if item["id"] == source_show_id
        )
        assert show["progressions"] == [
            {
                "target_show_id": national_final["show_id"],
                "target_short_name": "f",
                "target_name": "Final",
                "qualifier_count": 5,
                "priority": 1,
            }
        ]

        with db.cursor() as cursor:
            cursor.execute("SAVEPOINT progression_cycle")
            with pytest.raises(psycopg.errors.RaiseException):
                cursor.execute(
                    """
                    INSERT INTO show_progression (
                        source_show_id, target_show_id, qualifier_count, priority
                    ) VALUES (%s, %s, 1, 1)
                    """,
                    (national_final["show_id"], source_show_id),
                )
            cursor.execute("ROLLBACK TO SAVEPOINT progression_cycle")
    finally:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM show WHERE id = %s", (source_show_id,))
        db.commit()


def test_year_scoped_nfs_page_lists_event(client, national_final):
    response = client.get("/year/2025/nfs", headers={"Accept": "application/json"})
    assert response.status_code == 200
    listed = response.get_json()["national_finals"]
    assert any(
        nf["name"] == "Test Spanish Final" and nf["short_name"] == "test-es"
        for nf in listed
    )


def test_public_and_management_views_are_separate(client, db, national_final):
    public = client.get(
        "/year/2025/nfs/test-es", headers={"Accept": "application/json"}
    )
    assert public.status_code == 200
    public_data = public.get_json()
    assert public_data["can_manage"] is False
    assert public_data["metadata_countries"] == []
    assert "lineup_issues" not in public_data
    assert "unassigned_lineup_issue" not in public_data

    forbidden = client.get(
        "/year/2025/nfs/test-es/manage", headers={"Accept": "application/json"}
    )
    assert forbidden.status_code == 403

    session_id = _set_session(client, db, 2)
    management = client.get(
        "/year/2025/nfs/test-es/manage", headers={"Accept": "application/json"}
    )
    assert management.status_code == 200
    management_data = management.get_json()
    assert management_data["can_manage"] is True
    assert management_data["can_reassign_owner"] is False
    assert management_data["metadata_owners"] == []
    assert management_data["metadata_countries"]
    assert "lineup_issues" not in management_data
    assert "unassigned_lineup_issue" not in management_data

    public_as_owner = client.get(
        "/year/2025/nfs/test-es", headers={"Accept": "application/json"}
    )
    assert public_as_owner.get_json()["can_manage"] is True
    assert public_as_owner.get_json()["metadata_countries"] == []
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
    db.commit()


def test_finished_national_final_exposes_progression_ranking_and_entry_links(
    client, db, national_final
):
    song_ids = []
    semifinal_id = None
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING"
            )
            cursor.execute(
                """
                INSERT INTO show (
                    year_id, point_system_id, show_type, show_number, status,
                    national_final_id
                ) VALUES (2025, %s, 'sf', 1, 'full', %s)
                RETURNING id
                """,
                (national_final["point_system_id"], national_final["id"]),
            )
            semifinal_id = cursor.fetchone()["id"]
            cursor.execute(
                "UPDATE show SET status = 'full' WHERE id = %s",
                (national_final["show_id"],),
            )
            cursor.execute(
                """
                INSERT INTO show_progression (
                    source_show_id, target_show_id, qualifier_count, priority
                ) VALUES (%s, %s, 1, 1)
                """,
                (semifinal_id, national_final["show_id"]),
            )

            for position, title in enumerate(
                ("Semifinalist", "Non-qualifier", "Direct finalist"), 1
            ):
                cursor.execute(
                    """
                    INSERT INTO song (
                        year_id, country_id, entry_number, main_participant
                    ) VALUES (2025, 'ES', %s, false)
                    RETURNING id
                    """,
                    (300 + position,),
                )
                song_id = cursor.fetchone()["id"]
                song_ids.append(song_id)
                cursor.execute(
                    """
                    INSERT INTO song_data (
                        song_id, title, artist_credit_set_id, submitter_id
                    ) VALUES (%s, %s, test_artist_credit('Archive Artist'), 3)
                    """,
                    (song_id, title),
                )
                cursor.execute(
                    """
                    INSERT INTO national_final_song (national_final_id, song_id)
                    VALUES (%s, %s)
                    """,
                    (national_final["id"], song_id),
                )

            cursor.executemany(
                """
                INSERT INTO song_show (song_id, show_id, running_order)
                VALUES (%s, %s, %s)
                """,
                [
                    (song_ids[0], semifinal_id, 1),
                    (song_ids[1], semifinal_id, 2),
                    (song_ids[0], national_final["show_id"], 1),
                    (song_ids[2], national_final["show_id"], 2),
                ],
            )
            cursor.execute(
                "SET CONSTRAINTS trg_process_show_result_refresh_queue IMMEDIATE"
            )
            cursor.execute(
                "DELETE FROM country_show_results WHERE show_id IN (%s, %s)",
                (semifinal_id, national_final["show_id"]),
            )

            def add_result(show_id, short_name, song_id, order, place, points):
                percentage = 100 if points == 12 else 83.33
                cursor.execute(
                    """
                    INSERT INTO country_show_results (
                        country_id, country_name, show_id, show_name, short_name,
                        year_id, song_id, running_order, total_points,
                        total_votes_received, point_distribution, place,
                        total_countries, placement_percentage,
                        max_possible_points, points_percentage,
                        adjusted_points_percentage, adjusted_max_possible_points,
                        points_midpoint, max_pts, total_voters, result_mode
                    ) VALUES (
                        'ES', 'Spain', %s, 'Show', %s, 2025, %s, %s, %s,
                        1, JSONB_BUILD_OBJECT(%s::text, 1), %s, 2, %s,
                        12, %s, %s, 12, 11, 12, 1, 'official'
                    )
                    """,
                    (
                        show_id,
                        short_name,
                        song_id,
                        order,
                        points,
                        points,
                        place,
                        100 if place == 1 else 0,
                        percentage,
                        percentage,
                    ),
                )

            add_result(semifinal_id, "sf1", song_ids[0], 1, 1, 12)
            add_result(semifinal_id, "sf1", song_ids[1], 2, 2, 10)
            add_result(national_final["show_id"], "f", song_ids[0], 1, 2, 10)
            add_result(national_final["show_id"], "f", song_ids[2], 2, 1, 12)
            cursor.execute(
                "UPDATE national_final SET status = 'finished' WHERE id = %s",
                (national_final["id"],),
            )
        db.commit()

        archive = client.get(
            "/year/2025/nfs/test-es", headers={"Accept": "application/json"}
        )
        assert archive.status_code == 200
        data = archive.get_json()
        assert [show["short_name"] for show in data["shows"]] == ["sf1", "f"]
        assert {
            int(song_id): ranking["overall_place"]
            for song_id, ranking in data["rankings"].items()
        } == {
            song_ids[2]: 1,
            song_ids[0]: 2,
            song_ids[1]: 3,
        }
        assert data["stage_results"][str(song_ids[0])][
            str(national_final["show_id"])
        ]["place"] == 2

        entry = client.get(
            "/country/es/2025/301", headers={"Accept": "application/json"}
        )
        assert entry.status_code == 200
        assert entry.get_json()["song"]["id"] == song_ids[0]
    finally:
        db.rollback()
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM country_show_results WHERE song_id = ANY(%s)",
                (song_ids,),
            )
            cursor.execute("DELETE FROM show_qualifier WHERE song_id = ANY(%s)", (song_ids,))
            cursor.execute("DELETE FROM song_show WHERE song_id = ANY(%s)", (song_ids,))
            cursor.execute(
                "DELETE FROM national_final_song WHERE song_id = ANY(%s)", (song_ids,)
            )
            if semifinal_id is not None:
                cursor.execute("DELETE FROM show WHERE id = %s", (semifinal_id,))
            cursor.execute("DELETE FROM song_data WHERE song_id = ANY(%s)", (song_ids,))
            cursor.execute("DELETE FROM song WHERE id = ANY(%s)", (song_ids,))
            cursor.execute(
                "UPDATE national_final SET status = 'draft' WHERE id = %s",
                (national_final["id"],),
            )
        db.commit()


def test_owner_can_edit_national_final_metadata(client, db, national_final):
    session_id = _set_session(client, db, 2)
    try:
        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={
                "action": "update_metadata",
                "name": "Independent Test Final",
                "owner_country_id": "",
                "short_name": "independent-nf",
                "owner_id": "3",
            },
        )

        assert response.status_code == 302
        assert response.location.endswith("/year/2025/nfs/independent-nf/manage")
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT name, owner_id, owner_country_id, short_name "
                "FROM national_final WHERE id = %s",
                (national_final["id"],),
            )
            edited = cursor.fetchone()
        assert edited == {
            "name": "Independent Test Final",
            "owner_id": 2,
            "owner_country_id": None,
            "short_name": "independent-nf",
        }
        public = client.get(
            "/year/2025/nfs/independent-nf",
            headers={"Accept": "application/json"},
        )
        assert public.get_json()["nf"]["name"] == "Independent Test Final"
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE national_final SET name = 'Test Spanish Final', "
                "owner_country_id = 'ES', short_name = 'test-es' WHERE id = %s",
                (national_final["id"],),
            )
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_country_backed_metadata_derives_identifier(client, db, national_final):
    session_id = _set_session(client, db, 1)
    try:
        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={
                "action": "update_metadata",
                "name": "French Test Final",
                "owner_id": "2",
                "owner_country_id": "FR",
                "short_name": "ignored",
            },
        )

        assert response.status_code == 302
        assert response.location.endswith("/year/2025/nfs/fr/manage")
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT owner_country_id, short_name FROM national_final WHERE id = %s",
                (national_final["id"],),
            )
            assert cursor.fetchone() == {"owner_country_id": "FR", "short_name": "fr"}
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE national_final SET name = 'Test Spanish Final', "
                "owner_country_id = 'ES', short_name = 'test-es' WHERE id = %s",
                (national_final["id"],),
            )
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_vote_index_groups_main_and_national_final_shows(
    client, db, national_final
):
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE national_final SET status = 'voting' WHERE id = %s
            """,
            (national_final["id"],),
        )
        cursor.execute(
            "UPDATE show SET voting_opens = CURRENT_TIMESTAMP WHERE id = %s",
            (national_final["show_id"],),
        )
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_type, show_number, status,
                voting_opens
            ) VALUES (2025, %s, 'sf', 99, 'none', CURRENT_TIMESTAMP)
            """,
            (national_final["point_system_id"],),
        )
    db.commit()

    try:
        response = client.get("/vote", headers={"Accept": "application/json"})

        assert response.status_code == 200
        sections = response.get_json()["year_sections"]
        sections_by_name = {section["name"]: section["shows"] for section in sections}
        assert any(show["short_name"] == "2025-sf99" for show in sections_by_name["2025"])
        nf_shows = sections_by_name["2025: Test Spanish Final"]
        assert any(
            show["short_name"] == "2025-test-es-f" and show["predictions_open"]
            for show in nf_shows
        )
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM show "
                "WHERE year_id = 2025 AND national_final_id IS NULL "
                "AND short_name = 'sf99'"
            )
            cursor.execute(
                "UPDATE show SET voting_opens = NULL WHERE id = %s",
                (national_final["show_id"],),
            )
            cursor.execute(
                "UPDATE national_final SET status = 'draft' WHERE id = %s",
                (national_final["id"],),
            )
        db.commit()


def test_country_with_nf_is_unavailable_in_nf_creation(
    client, db, national_final
):
    session_id = _set_session(client, db, 1)

    response = client.get(
        "/admin/manage/2025/create/nf",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    country_ids = {country["id"] for country in response.get_json()["countries"]}
    assert "ES" not in country_ids
    assert "FR" in country_ids
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
    db.commit()


def test_national_final_is_created_without_creating_a_show(
    client, db, national_final
):
    session_id = _set_session(client, db, 1)
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM show WHERE year_id = 2025")
        show_count = cursor.fetchone()["count"]

    try:
        response = client.post(
            "/admin/manage/2025/create/nf",
            data={
                "name": "Independent National Final",
                "owner_id": "2",
                "owner_country_id": "",
                "short_name": "independent-test",
            },
        )

        assert response.status_code == 302
        assert response.location.endswith("/admin/manage/2025/create/show")
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM national_final "
                "WHERE year_id = 2025 AND short_name = 'independent-test'"
            )
            assert cursor.fetchone()["name"] == "Independent National Final"
            cursor.execute("SELECT COUNT(*) AS count FROM show WHERE year_id = 2025")
            assert cursor.fetchone()["count"] == show_count
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM national_final "
                "WHERE year_id = 2025 AND short_name = 'independent-test'"
            )
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_owner_can_edit_nf_candidate_from_entry_page(client, db, national_final):
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO song (year_id, country_id, entry_number, main_participant)
            VALUES (2025, 'ES', 42, false)
            RETURNING id
            """
        )
        song_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO song_data (
                song_id, title, artist_credit_set_id, submitter_id, sources
            ) VALUES (
                %s, 'NF Candidate', test_artist_credit('NF Artist'), 3,
                'https://example.com/source'
            )
            """,
            (song_id,),
        )
        cursor.execute(
            "INSERT INTO national_final_song (national_final_id, song_id) "
            "VALUES (%s, %s)",
            (national_final["id"], song_id),
        )
    db.commit()
    session_id = _set_session(client, db, 2)

    try:
        entry = client.get(
            "/country/es/2025/42", headers={"Accept": "text/html"}
        )

        assert entry.status_code == 200
        assert (
            f"national_final_id={national_final['id']}" in entry.get_data(as_text=True)
        )
        assert "entry_number=42" in entry.get_data(as_text=True)

        editor = client.get(
            "/member/submit",
            query_string={
                "year": 2025,
                "country": "es",
                "entry_number": 42,
                "national_final_id": national_final["id"],
            },
        )
        assert editor.status_code == 200

        updated = client.put(
            f"/api/song/{song_id}",
            json={
                "year": 2025,
                "country": "ES",
                "entry_number": 42,
                "national_final_id": national_final["id"],
                "title": "Edited NF Candidate",
                "artist": "NF Artist",
                "sources": "https://example.com/source",
                "languages": [20],
            },
        )
        assert updated.status_code == 200
        assert updated.get_json()["result"]["title"] == "Edited NF Candidate"
    finally:
        db.rollback()
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM national_final_song WHERE song_id = %s", (song_id,))
            cursor.execute("DELETE FROM song_status WHERE song_id = %s", (song_id,))
            cursor.execute("DELETE FROM song_data WHERE song_id = %s", (song_id,))
            cursor.execute("DELETE FROM song WHERE id = %s", (song_id,))
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_show_creation_context_lists_existing_point_system(
    client, db, national_final
):
    session_id = _set_session(client, db, 1)

    response = client.get(
        "/admin/manage/2025/create/show",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    point_systems = {
        system["id"]: system["points"]
        for system in response.get_json()["point_systems"]
    }
    assert point_systems[national_final["point_system_id"]] == [12, 10, 8]
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
    db.commit()


def test_show_form_suggests_the_next_nf_semifinal_number(
    client, db, national_final
):
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO show (
                year_id, point_system_id, show_type, show_number, status,
                national_final_id
            ) VALUES (2025, %s, 'sf', 1, 'none', %s)
            """,
            (national_final["point_system_id"], national_final["id"]),
        )
    db.commit()
    session_id = _set_session(client, db, 1)

    try:
        response = client.get(
            "/admin/manage/2025/create/show",
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        events = {
            event["id"]: event
            for event in response.get_json()["national_finals"]
        }
        assert events[national_final["id"]]["next_semifinal_number"] == 2
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM show "
                "WHERE national_final_id = %s AND short_name = 'sf1'",
                (national_final["id"],),
            )
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_show_creation_accepts_an_erased_semifinal_number(
    client, db, national_final
):
    session_id = _set_session(client, db, 1)

    try:
        response = client.post(
            "/admin/manage/2025/create/show",
            data={
                "national_final_id": str(national_final["id"]),
                "show_type": "sf",
                "show_number": "",
                "point_system_id": str(national_final["point_system_id"]),
            },
        )

        assert response.status_code == 302
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT show_number, short_name, show_name FROM show
                WHERE national_final_id = %s AND short_name = 'sf'
                """,
                (national_final["id"],),
            )
            assert cursor.fetchone() == {
                "show_number": None,
                "short_name": "sf",
                "show_name": "Semi-Final",
            }
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM show "
                "WHERE national_final_id = %s AND short_name = 'sf'",
                (national_final["id"],),
            )
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_show_can_be_created_for_an_existing_national_final(
    client, db, national_final
):
    session_id = _set_session(client, db, 1)

    try:
        response = client.post(
            "/admin/manage/2025/create/show",
            data=MultiDict(
                [
                    ("national_final_id", str(national_final["id"])),
                    ("show_type", "sf"),
                    ("show_number", "1"),
                    ("point_system_id", str(national_final["point_system_id"])),
                    ("progression_target_id", str(national_final["show_id"])),
                    ("progression_count", "6"),
                ]
            ),
        )

        assert response.status_code == 302
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT source.show_name, progression.target_show_id,
                       progression.qualifier_count
                FROM show AS source
                JOIN show_progression AS progression
                  ON progression.source_show_id = source.id
                WHERE source.national_final_id = %s AND source.short_name = 'sf1'
                """,
                (national_final["id"],),
            )
            assert cursor.fetchone() == {
                "show_name": "Semi-Final 1",
                "target_show_id": national_final["show_id"],
                "qualifier_count": 6,
            }
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM show "
                "WHERE national_final_id = %s AND short_name = 'sf1'",
                (national_final["id"],),
            )
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_show_creation_accepts_arbitrary_progression_rows(
    client, db, national_final
):
    session_id = _set_session(client, db, 1)
    target_ids = []
    try:
        with db.cursor() as cursor:
            for show_number in range(20, 24):
                cursor.execute(
                    """
                    INSERT INTO show (
                        year_id, point_system_id, show_type, show_number,
                        status, national_final_id
                    ) VALUES (2025, %s, 'sf', %s, 'none', %s)
                    RETURNING id
                    """,
                    (
                        national_final["point_system_id"],
                        show_number,
                        national_final["id"],
                    ),
                )
                target_ids.append(cursor.fetchone()["id"])
        db.commit()

        form = MultiDict(
            [
                ("national_final_id", str(national_final["id"])),
                ("show_type", "sc"),
                ("point_system_id", str(national_final["point_system_id"])),
            ]
        )
        for target_id, count in zip(target_ids, range(1, 5), strict=True):
            form.add("progression_target_id", str(target_id))
            form.add("progression_count", str(count))

        response = client.post("/admin/manage/2025/create/show", data=form)

        assert response.status_code == 302
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT progression.target_show_id, progression.qualifier_count,
                       progression.priority
                FROM show AS source
                JOIN show_progression AS progression
                  ON progression.source_show_id = source.id
                WHERE source.national_final_id = %s AND source.short_name = 'sc'
                ORDER BY progression.priority
                """,
                (national_final["id"],),
            )
            assert cursor.fetchall() == [
                {
                    "target_show_id": target_id,
                    "qualifier_count": count,
                    "priority": count,
                }
                for target_id, count in zip(target_ids, range(1, 5), strict=True)
            ]
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM show WHERE national_final_id = %s AND short_name = 'sc'",
                (national_final["id"],),
            )
            cursor.execute("DELETE FROM show WHERE id = ANY(%s)", (target_ids,))
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_country_with_nf_is_unavailable_in_main_submission_form(
    client, db, national_final
):
    session_id = _set_session(client, db, 2)

    response = client.get("/member/submit/2025")

    assert response.status_code == 200
    available = {
        country["cc"]
        for group in ("own", "placeholder")
        for country in response.json["countries"][group]
    }
    assert "ES" not in available
    assert "FR" in available
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
    db.commit()


def test_main_submission_api_rejects_country_with_nf(
    client, bob_headers, national_final
):
    response = client.post(
        "/api/song",
        headers=bob_headers,
        json={
            "year": 2025,
            "country": "ES",
            "title": "Direct Entry",
            "artist": "Artist",
            "sources": "https://example.com",
            "languages": [20],
        },
    )

    assert response.status_code == 403
    assert "national final" in response.get_json()["error"]["description"].lower()


def test_ongoing_nf_is_a_tbd_entry_on_year_overview(client, national_final):
    response = client.get("/year/2025", headers={"Accept": "application/json"})

    assert response.status_code == 200
    ongoing = response.get_json()["ongoing_national_finals"]
    assert any(
        nf["short_name"] == "test-es" and nf["country_id"] == "ES"
        for nf in ongoing
    )


def test_owner_can_create_multiple_candidates_as_non_main_participants(
    client, db, bob_headers, national_final
):
    candidate = {
        "year": 2025,
        "country": "ES",
        "artist": "Artist",
        "sources": "https://example.com",
        "languages": [20],
        "national_final_id": national_final["id"],
    }
    first = client.post(
        "/api/song",
        headers=bob_headers,
        json={**candidate, "title": "First candidate"},
    )
    second = client.post(
        "/api/song",
        headers=bob_headers,
        json={**candidate, "title": "Second candidate"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    results = [first.get_json()["result"], second.get_json()["result"]]
    assert [result["entry_number"] for result in results] == [1, 2]
    song_ids = [result["id"] for result in results]
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT song.id, song.main_participant,
                   national_final_song.national_final_id
            FROM song
            JOIN national_final_song ON national_final_song.song_id = song.id
            WHERE song.id = ANY(%s)
            ORDER BY song.entry_number
            """,
            (song_ids,),
        )
        assert cursor.fetchall() == [
            {
                "id": song_id,
                "main_participant": False,
                "national_final_id": national_final["id"],
            }
            for song_id in song_ids
        ]


def test_country_backed_nf_rejects_other_country(client, bob_headers, national_final):
    response = client.post(
        "/api/song",
        headers=bob_headers,
        json={
            "year": 2025,
            "country": "FR",
            "title": "Wrong Country",
            "artist": "Artist",
            "sources": "https://example.com",
            "languages": [20],
            "national_final_id": national_final["id"],
        },
    )
    assert response.status_code == 400


def test_owner_can_promote_candidate(client, db, national_final):
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO song (year_id, country_id, entry_number, main_participant) "
            "VALUES (2025, 'ES', 90, false) RETURNING id"
        )
        song_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO national_final_song (national_final_id, song_id) VALUES (%s, %s)",
            (national_final["id"], song_id),
        )
    db.commit()
    session_id = _set_session(client, db, 2)

    response = client.post(
        "/year/2025/nfs/test-es/manage",
        data={
            "action": "set_main_participant",
            "song_id": song_id,
            "enabled": "true",
        },
    )
    assert response.status_code == 302
    with db.cursor() as cursor:
        cursor.execute("SELECT main_participant FROM song WHERE id = %s", (song_id,))
        assert cursor.fetchone()["main_participant"] is True
        cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
    db.commit()


def test_nf_ballot_ignores_country_and_uses_capacity_exception(db, national_final):
    candidates = []
    with db.cursor() as cursor:
        for entry_number, submitter_id in [(101, 2), (102, 3), (103, 3), (104, 3)]:
            cursor.execute(
                """
                INSERT INTO song (year_id, country_id, entry_number, main_participant)
                VALUES (2025, 'ES', %s, false) RETURNING id
                """,
                (entry_number,),
            )
            song_id = cursor.fetchone()["id"]
            candidates.append((song_id, submitter_id))
            cursor.execute(
                """
                INSERT INTO song_data (
                    song_id, country_id, year_id, entry_number, submitter_id,
                    title, artist_credit_set_id
                ) VALUES (
                    %s, 'ES', 2025, %s, %s, %s,
                    test_artist_credit('Artist')
                )
                """,
                (song_id, entry_number, submitter_id, f"Candidate {entry_number}"),
            )
            cursor.execute(
                "INSERT INTO national_final_song (national_final_id, song_id) VALUES (%s, %s)",
                (national_final["id"], song_id),
            )
            cursor.execute(
                "INSERT INTO song_show (show_id, song_id, running_order) VALUES (%s, %s, %s)",
                (national_final["show_id"], song_id, entry_number - 100),
            )
    db.commit()

    bob_song = candidates[0][0]
    carol_song = candidates[1][0]
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT rule_kind, rule_reason FROM ballot_entry_rule(%s, 'official', 2, 'ES', %s)",
            (national_final["show_id"], bob_song),
        )
        assert cursor.fetchone() == {"rule_kind": "FORBIDDEN", "rule_reason": "owner"}
        cursor.execute(
            "SELECT rule_kind, rule_reason FROM ballot_entry_rule(%s, 'official', 2, 'ES', %s)",
            (national_final["show_id"], carol_song),
        )
        assert cursor.fetchone() == {"rule_kind": "NORMAL", "rule_reason": None}
        cursor.execute(
            "SELECT rule_kind, rule_reason FROM ballot_entry_rule(%s, 'official', 3, 'ES', %s)",
            (national_final["show_id"], carol_song),
        )
        assert cursor.fetchone() == {"rule_kind": "NORMAL", "rule_reason": None}


def test_nf_lifecycle_controls_voting_and_closes_child_shows(client, db, national_final):
    session_id = _set_session(client, db, 2)
    lineup_song_ids = []
    try:
        response = client.get(
            "/vote/2025-test-es-f", headers={"Accept": "application/json"}
        )
        assert response.status_code == 400

        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={"action": "set_lifecycle", "lifecycle_status": "submissions"},
        )
        assert response.status_code == 302

        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={"action": "open_voting", "show_id": national_final["show_id"]},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 400
        assert "lineup_empty" in {
            issue["code"] for issue in response.get_json()["lineup_issues"]
        }

        with db.cursor() as cursor:
            for position in range(1, 4):
                cursor.execute(
                    """
                    INSERT INTO song (
                        year_id, country_id, entry_number, main_participant
                    ) VALUES (2025, 'ES', %s, false)
                    RETURNING id
                    """,
                    (200 + position,),
                )
                song_id = cursor.fetchone()["id"]
                lineup_song_ids.append(song_id)
                cursor.execute(
                    """
                    INSERT INTO song_data (
                        song_id, title, artist_credit_set_id, submitter_id
                    ) VALUES (%s, %s, test_artist_credit('Artist'), 3)
                    """,
                    (song_id, f"Lifecycle candidate {position}"),
                )
                cursor.execute(
                    """
                    INSERT INTO national_final_song (national_final_id, song_id)
                    VALUES (%s, %s)
                    """,
                    (national_final["id"], song_id),
                )
                cursor.execute(
                    """
                    INSERT INTO song_show (show_id, song_id, running_order)
                    VALUES (%s, %s, %s)
                    """,
                    (national_final["show_id"], song_id, position),
                )
        db.commit()

        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={"action": "open_voting", "show_id": national_final["show_id"]},
        )
        assert response.status_code == 302
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM national_final WHERE id = %s", (national_final["id"],)
            )
            assert cursor.fetchone()["status"] == "voting"

        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={"action": "set_lifecycle", "lifecycle_status": "finished"},
        )
        assert response.status_code == 302
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT voting_closes FROM show WHERE id = %s", (national_final["show_id"],)
            )
            assert cursor.fetchone()["voting_closes"] is not None
    finally:
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM song_show WHERE song_id = ANY(%s)", (lineup_song_ids,)
            )
            cursor.execute(
                "DELETE FROM national_final_song WHERE song_id = ANY(%s)",
                (lineup_song_ids,),
            )
            cursor.execute(
                "DELETE FROM song_data WHERE song_id = ANY(%s)", (lineup_song_ids,)
            )
            cursor.execute("DELETE FROM song WHERE id = ANY(%s)", (lineup_song_ids,))
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_cancelled_nf_releases_country_and_removes_tbd(client, db, national_final):
    session_id = _set_session(client, db, 2)
    try:
        response = client.post(
            "/year/2025/nfs/test-es/manage",
            data={"action": "set_lifecycle", "lifecycle_status": "cancelled"},
        )
        assert response.status_code == 302

        response = client.get("/member/submit/2025")
        available = {
            country["cc"]
            for group in ("own", "placeholder")
            for country in response.json["countries"][group]
        }
        assert "ES" in available

        response = client.get(
            "/year/2025", headers={"Accept": "application/json"}
        )
        assert all(
            nf["short_name"] != "test-es"
            for nf in response.get_json()["ongoing_national_finals"]
        )
    finally:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()
