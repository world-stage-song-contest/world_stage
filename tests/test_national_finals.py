import string

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.fixture()
def national_final(db):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
        point_system_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM point_system"
        ).fetchone()["id"]
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (%s, 3)",
            (point_system_id,),
        )
        first_point_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM point"
        ).fetchone()["id"]
        cursor.executemany(
            """INSERT INTO point (id, point_system_id, place, score)
               VALUES (%s, %s, %s, %s)""",
            [
                (first_point_id, point_system_id, 1, 12),
                (first_point_id + 1, point_system_id, 2, 10),
                (first_point_id + 2, point_system_id, 3, 8),
            ],
        )
        national_final_id = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM national_final"
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO national_final (
                   id, year_id, owner_id, owner_country_id, short_name, name
               ) VALUES (
                   %s, 2025, 2, 'ES', 'test-es', 'Test Spanish Final'
               )""",
            (national_final_id,),
        )
        show_id = cursor.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM show").fetchone()[
            "id"
        ]
        cursor.execute(
            """INSERT INTO show (
                   id, year_id, point_system_id, show_type, status,
                   national_final_id
               ) VALUES (%s, 2025, %s, 'f', 'none', %s)""",
            (show_id, point_system_id, national_final_id),
        )
    db.commit()
    return {
        "id": national_final_id,
        "show_id": show_id,
        "point_system_id": point_system_id,
    }


def _add_candidate(db, national_final_id, *, submitter=3, country="ES"):
    with db.cursor() as cursor:
        entry_number = cursor.execute(
            """SELECT COALESCE(MAX(entry_number), 0) + 1 AS entry_number
               FROM song WHERE year_id = 2025 AND country_id = %s""",
            (country,),
        ).fetchone()["entry_number"]
        song_id = cursor.execute(
            """INSERT INTO song (
                   year_id, country_id, entry_number, main_participant
               ) VALUES (2025, %s, %s, false) RETURNING id""",
            (country, entry_number),
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id
               ) VALUES (
                   %s, %s, %s, test_artist_credit('Candidate Artist')
               )""",
            (song_id, submitter, f"Candidate {song_id}"),
        )
        cursor.execute(
            """INSERT INTO national_final_song (national_final_id, song_id)
               VALUES (%s, %s)""",
            (national_final_id, song_id),
        )
    db.commit()
    return song_id


def _remove_candidates(db, song_ids):
    db.rollback()
    db.execute("DELETE FROM vote WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song_show WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM national_final_song WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song_status WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song_data WHERE song_id = ANY(%s)", (song_ids,))
    db.execute("DELETE FROM song WHERE id = ANY(%s)", (song_ids,))
    db.commit()


def _remove_shows(db, show_ids):
    db.rollback()
    db.execute(
        """DELETE FROM show_progression
           WHERE source_show_id = ANY(%s) OR target_show_id = ANY(%s)""",
        (show_ids, show_ids),
    )
    db.execute("DELETE FROM show WHERE id = ANY(%s)", (show_ids,))
    db.commit()


def test_national_final_show_keys_and_order_are_compositional(client, db, national_final):
    @settings(max_examples=8, deadline=None)
    @given(semifinals=st.integers(0, 4), repechage=st.booleans())
    def property_test(semifinals, repechage):
        extra_ids = []
        with db.cursor() as cursor:
            for number in range(1, semifinals + 1):
                extra_ids.append(
                    cursor.execute(
                        """INSERT INTO show (
                               year_id, point_system_id, show_type, show_number,
                               status, national_final_id
                           ) VALUES (2025, %s, 'sf', %s, 'none', %s)
                           RETURNING id""",
                        (
                            national_final["point_system_id"],
                            number,
                            national_final["id"],
                        ),
                    ).fetchone()["id"]
                )
            if repechage:
                extra_ids.append(
                    cursor.execute(
                        """INSERT INTO show (
                               year_id, point_system_id, show_type, status,
                               national_final_id
                           ) VALUES (2025, %s, 'sc', 'none', %s)
                           RETURNING id""",
                        (national_final["point_system_id"], national_final["id"]),
                    ).fetchone()["id"]
                )
        db.commit()
        try:
            public = client.get("/year/2025/nfs/test-es", headers={"Accept": "application/json"})
            assert public.status_code == 200
            expected_local = [f"sf{number}" for number in range(1, semifinals + 1)]
            if repechage:
                expected_local.append("sc")
            expected_local.append("f")
            assert [show["short_name"] for show in public.get_json()["shows"]] == expected_local

            discovered = {
                show["id"]: show for show in client.get("/api/show?year=2025").get_json()["result"]
            }
            for show_id in [national_final["show_id"], *extra_ids]:
                show = discovered[show_id]
                assert show["key"] == f"2025-test-es-{show['local_short_name']}"
                assert show["short_name"] == f"test-es-{show['local_short_name']}"
        finally:
            if extra_ids:
                _remove_shows(db, extra_ids)

    property_test()


def test_open_votings_put_every_main_year_show_before_national_finals(
    client, db, national_final
):
    @settings(max_examples=6, deadline=None)
    @given(main_show_count=st.integers(1, 4))
    def property_test(main_show_count):
        main_show_ids = []
        with db.cursor() as cursor:
            for number in range(1, main_show_count + 1):
                main_show_ids.append(
                    cursor.execute(
                        """INSERT INTO show (
                               year_id, point_system_id, show_type, show_number,
                               status, voting_opens
                           ) VALUES (2024, %s, 'sf', %s, 'none', CURRENT_TIMESTAMP)
                           RETURNING id""",
                        (national_final["point_system_id"], number),
                    ).fetchone()["id"]
                )
            cursor.execute(
                "UPDATE national_final SET status = 'voting' WHERE id = %s",
                (national_final["id"],),
            )
            cursor.execute(
                "UPDATE show SET voting_opens = CURRENT_TIMESTAMP WHERE id = %s",
                (national_final["show_id"],),
            )
        db.commit()
        try:
            relevant_ids = {*main_show_ids, national_final["show_id"]}
            votings = [
                show
                for show in client.get("/api/voting/open").get_json()["result"]
                if show["id"] in relevant_ids
            ]
            assert [show["national_final_short_name"] is None for show in votings] == [
                True
            ] * main_show_count + [False]
        finally:
            _remove_shows(db, main_show_ids)
            db.execute(
                "UPDATE show SET voting_opens = NULL WHERE id = %s",
                (national_final["show_id"],),
            )
            db.execute(
                "UPDATE national_final SET status = 'draft' WHERE id = %s",
                (national_final["id"],),
            )
            db.commit()

    property_test()


def test_show_progression_qualifies_exactly_the_configured_prefix_and_is_acyclic(
    client, db, national_final
):
    @settings(max_examples=8, deadline=None)
    @given(qualifier_count=st.integers(1, 12), show_number=st.integers(10, 99))
    def property_test(qualifier_count, show_number):
        with db.cursor() as cursor:
            source_id = cursor.execute(
                """INSERT INTO show (
                       year_id, point_system_id, show_type, show_number,
                       status, national_final_id
                   ) VALUES (2025, %s, 'sf', %s, 'none', %s)
                   RETURNING id""",
                (
                    national_final["point_system_id"],
                    show_number,
                    national_final["id"],
                ),
            ).fetchone()["id"]
            cursor.execute(
                """INSERT INTO show_progression (
                       source_show_id, target_show_id, qualifier_count, priority
                   ) VALUES (%s, %s, %s, 1)""",
                (source_id, national_final["show_id"], qualifier_count),
            )
        db.commit()
        try:
            statuses = db.execute(
                """SELECT show_progression_status(%s, place) AS status
                   FROM generate_series(1, %s) AS place ORDER BY place""",
                (source_id, qualifier_count + 2),
            ).fetchall()
            assert [row["status"] for row in statuses] == ["f"] * qualifier_count + [
                "nq",
                "nq",
            ]
            with db.cursor() as cursor:
                cursor.execute("SAVEPOINT cycle")
                with pytest.raises(psycopg.errors.RaiseException):
                    cursor.execute(
                        """INSERT INTO show_progression (
                               source_show_id, target_show_id,
                               qualifier_count, priority
                           ) VALUES (%s, %s, 1, 1)""",
                        (national_final["show_id"], source_id),
                    )
                cursor.execute("ROLLBACK TO SAVEPOINT cycle")
        finally:
            _remove_shows(db, [source_id])

    property_test()


def test_only_the_owner_can_manage_metadata(client, db, national_final, login):
    slug_text = st.text(alphabet=string.ascii_lowercase, min_size=2, max_size=12)

    @settings(max_examples=8, deadline=None)
    @given(actor=st.sampled_from([None, 2, 3]), suffix=slug_text)
    def property_test(actor, suffix):
        db.execute(
            """UPDATE national_final
               SET name = 'Test Spanish Final', owner_country_id = 'ES',
                   short_name = 'test-es'
               WHERE id = %s""",
            (national_final["id"],),
        )
        db.commit()
        client.delete_cookie("session")
        if actor is not None:
            login(actor)

        management = client.get(
            "/year/2025/nfs/test-es/manage",
            headers={"Accept": "application/json"},
        )
        assert management.status_code == (200 if actor == 2 else 403)
        if actor == 2:
            response = client.post(
                "/year/2025/nfs/test-es/manage",
                data={
                    "action": "update_metadata",
                    "name": f"Generated {suffix}",
                    "owner_country_id": "",
                    "short_name": f"nf-{suffix}",
                    "owner_id": "3",
                },
            )
            assert response.status_code == 302
            assert db.execute(
                """SELECT name, owner_id, owner_country_id, short_name
                   FROM national_final WHERE id = %s""",
                (national_final["id"],),
            ).fetchone() == {
                "name": f"Generated {suffix}",
                "owner_id": 2,
                "owner_country_id": None,
                "short_name": f"nf-{suffix}",
            }

    property_test()


def test_candidate_creation_respects_event_country_and_main_entry_reservation(
    client, db, bob_headers, national_final
):
    @settings(max_examples=9, deadline=None)
    @given(
        kind=st.sampled_from(["candidate", "wrong-country", "main-entry"]), count=st.integers(1, 3)
    )
    def property_test(kind, count):
        created = []
        payload = {
            "year": 2025,
            "country": "FR" if kind == "wrong-country" else "ES",
            "artist": "Artist",
            "sources": "https://example.test/source",
            "languages": [20],
        }
        if kind != "main-entry":
            payload["national_final_id"] = national_final["id"]
        try:
            for index in range(count):
                response = client.post(
                    "/api/song",
                    headers=bob_headers,
                    json={**payload, "title": f"Generated candidate {index}"},
                )
                expected = 201 if kind == "candidate" else (400 if kind == "wrong-country" else 403)
                assert response.status_code == expected
                if response.status_code == 201:
                    created.append(response.get_json()["result"]["id"])
            if kind == "candidate":
                rows = db.execute(
                    """SELECT song.id, song.main_participant,
                              nf_song.national_final_id
                       FROM song
                       JOIN national_final_song AS nf_song
                         ON nf_song.song_id = song.id
                       WHERE song.id = ANY(%s) ORDER BY song.entry_number""",
                    (created,),
                ).fetchall()
                assert rows == [
                    {
                        "id": song_id,
                        "main_participant": False,
                        "national_final_id": national_final["id"],
                    }
                    for song_id in created
                ]
        finally:
            if created:
                _remove_candidates(db, created)

    property_test()


def test_nf_ballot_relaxes_ownership_only_when_required_for_ballot_capacity(db, national_final):
    @settings(max_examples=8, deadline=None)
    @given(owned_count=st.integers(1, 5))
    def property_test(owned_count):
        candidates = [
            _add_candidate(
                db,
                national_final["id"],
                submitter=2 if index < owned_count else 3,
            )
            for index in range(5)
        ]
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, %s)""",
                [
                    (national_final["show_id"], song_id, position)
                    for position, song_id in enumerate(candidates, start=1)
                ],
            )
        db.commit()
        try:
            rule = db.execute(
                """SELECT rule_kind, rule_reason
                   FROM ballot_entry_rule(%s, 'official', 2, 'ES', %s)""",
                (national_final["show_id"], candidates[0]),
            ).fetchone()
            enough_non_owned = len(candidates) - owned_count >= 3
            assert rule == (
                {"rule_kind": "FORBIDDEN", "rule_reason": "owner"}
                if enough_non_owned
                else {"rule_kind": "NORMAL", "rule_reason": None}
            )
        finally:
            _remove_candidates(db, candidates)

    property_test()


def test_lifecycle_controls_voting_and_country_reservation(client, db, national_final, login):
    login(2)

    @settings(max_examples=6, deadline=None)
    @given(terminal=st.sampled_from(["finished", "cancelled"]))
    def property_test(terminal):
        db.execute(
            """UPDATE national_final SET status = 'draft'
               WHERE id = %s""",
            (national_final["id"],),
        )
        db.execute(
            """UPDATE show SET voting_opens = NULL, voting_closes = NULL
               WHERE id = %s""",
            (national_final["show_id"],),
        )
        db.commit()
        candidates = [_add_candidate(db, national_final["id"], submitter=3) for _ in range(3)]
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, %s)""",
                [
                    (national_final["show_id"], song_id, position)
                    for position, song_id in enumerate(candidates, start=1)
                ],
            )
        db.commit()
        try:
            assert (
                client.post(
                    "/year/2025/nfs/test-es/manage",
                    data={"action": "set_lifecycle", "lifecycle_status": "submissions"},
                ).status_code
                == 302
            )
            assert (
                client.post(
                    "/year/2025/nfs/test-es/manage",
                    data={"action": "open_voting", "show_id": national_final["show_id"]},
                ).status_code
                == 302
            )
            assert (
                db.execute(
                    "SELECT status FROM national_final WHERE id = %s",
                    (national_final["id"],),
                ).fetchone()["status"]
                == "voting"
            )

            response = client.post(
                "/year/2025/nfs/test-es/manage",
                data={"action": "set_lifecycle", "lifecycle_status": terminal},
            )
            assert response.status_code == 302
            assert (
                db.execute(
                    "SELECT status FROM national_final WHERE id = %s",
                    (national_final["id"],),
                ).fetchone()["status"]
                == terminal
            )
            if terminal == "finished":
                assert (
                    db.execute(
                        "SELECT voting_closes IS NOT NULL AS closed FROM show WHERE id = %s",
                        (national_final["show_id"],),
                    ).fetchone()["closed"]
                    is True
                )
            else:
                available = client.get("/member/submit/2025").get_json()["countries"]
                available_codes = {
                    country["cc"]
                    for group in ("own", "placeholder")
                    for country in available[group]
                }
                assert "ES" in available_codes
                ongoing = client.get(
                    "/year/2025", headers={"Accept": "application/json"}
                ).get_json()["ongoing_national_finals"]
                assert all(event["short_name"] != "test-es" for event in ongoing)
        finally:
            _remove_candidates(db, candidates)

    property_test()


def test_existing_candidates_remain_editable_after_submissions_close(
    client, db, national_final, login
):
    @settings(max_examples=8, deadline=None)
    @given(
        status=st.sampled_from(["voting", "finished"]),
        actor=st.sampled_from([2, 3]),
    )
    def property_test(status, actor):
        song_id = _add_candidate(db, national_final["id"], submitter=3)
        candidate = db.execute(
            "SELECT entry_number FROM song WHERE id = %s", (song_id,)
        ).fetchone()
        db.execute(
            "UPDATE national_final SET status = %s WHERE id = %s",
            (status, national_final["id"]),
        )
        db.commit()
        client.delete_cookie("session")
        login(actor)
        try:
            query = (
                f"?national_final_id={national_final['id']}"
                f"&entry_number={candidate['entry_number']}"
            )
            countries = client.get(f"/member/submit/2025{query}")
            assert countries.status_code == 200
            assert set(countries.get_json()["countries"]) == {
                "own",
                "placeholder",
                "force_placeholder",
                "force_placeholder_reason",
            }

            edit_page = client.get(
                "/member/submit",
                query_string={
                    "national_final_id": national_final["id"],
                    "country": "ES",
                    "entry_number": candidate["entry_number"],
                },
                headers={"Accept": "application/json"},
            )
            assert edit_page.status_code == 200
            assert int(edit_page.get_json()["entry_number"]) == candidate["entry_number"]
        finally:
            _remove_candidates(db, [song_id])

    property_test()


def test_nf_result_routes_distinguish_main_and_numbered_entries(client, db, national_final):
    @settings(max_examples=6, deadline=None)
    @given(candidate_count=st.integers(2, 5), selected_index=st.integers(0, 20))
    def property_test(candidate_count, selected_index):
        db.rollback()
        db.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        db.commit()
        candidates = [
            _add_candidate(db, national_final["id"], submitter=3)
            for _ in range(candidate_count)
        ]
        selected_id = candidates[selected_index % candidate_count]
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE song SET main_participant = true WHERE id = %s",
                (selected_id,),
            )
            cursor.executemany(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, %s)""",
                [
                    (national_final["show_id"], song_id, position)
                    for position, song_id in enumerate(candidates, start=1)
                ],
            )
            cursor.execute(
                "UPDATE show SET status = 'full' WHERE id = %s",
                (national_final["show_id"],),
            )
        db.commit()
        try:
            response = client.get(
                "/year/2025/test-es-f/song/es",
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 200
            assert response.get_json()["song"]["id"] == selected_id

            rows = db.execute(
                "SELECT id, entry_number FROM song WHERE id = ANY(%s)",
                (candidates,),
            ).fetchall()
            for row in rows:
                response = client.get(
                    f"/year/2025/test-es-f/song/es/{row['entry_number']}",
                    headers={"Accept": "application/json"},
                )
                assert response.status_code == 200
                assert response.get_json()["song"]["id"] == row["id"]
        finally:
            _remove_candidates(db, candidates)
            db.execute(
                "UPDATE show SET status = 'none' WHERE id = %s",
                (national_final["show_id"],),
            )
            db.commit()

    property_test()


def test_histories_render_with_separate_nf_candidates(client, db, national_final):
    @settings(max_examples=6, deadline=None)
    @given(candidate_count=st.integers(1, 5))
    def property_test(candidate_count):
        candidates = [
            _add_candidate(db, national_final["id"], submitter=3)
            for _ in range(candidate_count)
        ]
        try:
            for path in (
                "/user/carol/submissions",
                "/country/es",
                "/artist/Candidate%20Artist",
            ):
                assert client.get(path).status_code == 200
        finally:
            _remove_candidates(db, candidates)

    property_test()


def test_main_selection_is_immutable_after_main_show_assignment(
    client, db, national_final, login
):
    login(2)

    @settings(max_examples=6, deadline=None)
    @given(candidate_count=st.integers(2, 5), selected_index=st.integers(0, 20))
    def property_test(candidate_count, selected_index):
        candidates = [
            _add_candidate(db, national_final["id"], submitter=3)
            for _ in range(candidate_count)
        ]
        selected_index %= candidate_count
        target_index = (selected_index + 1) % candidate_count
        selected_id = candidates[selected_index]
        target_id = candidates[target_index]
        with db.cursor() as cursor:
            cursor.execute("UPDATE song SET main_participant = true WHERE id = %s", (selected_id,))
            main_show_id = cursor.execute(
                """INSERT INTO show (
                       year_id, point_system_id, show_type, status
                   ) VALUES (2025, %s, 'sf', 'none') RETURNING id""",
                (national_final["point_system_id"],),
            ).fetchone()["id"]
            cursor.execute(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, 1)""",
                (main_show_id, selected_id),
            )
        db.commit()
        try:
            response = client.post(
                "/year/2025/nfs/test-es/manage",
                data={
                    "action": "set_main_participant",
                    "song_id": target_id,
                    "enabled": "true",
                },
            )
            assert response.status_code == 409
            selected = db.execute(
                """SELECT id FROM song
                   WHERE year_id = 2025 AND country_id = 'ES' AND main_participant"""
            ).fetchall()
            assert selected == [{"id": selected_id}]
        finally:
            _remove_candidates(db, candidates)
            _remove_shows(db, [main_show_id])

    property_test()
