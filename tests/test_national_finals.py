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
            assert public.get_json()["has_f"] is True
            assert public.get_json()["has_sf"] is (semifinals > 0)
            assert public.get_json()["has_sc"] is repechage

            discovered = {
                show["id"]: show for show in client.get("/api/show?year=2025").get_json()["result"]
            }
            for show_id in [national_final["show_id"], *extra_ids]:
                show = discovered[show_id]
                assert show["key"] == f"2025-test-es-{show['local_short_name']}"
                assert show["short_name"] == f"test-es-{show['local_short_name']}"
                assert show["display_name"] == f"Test Spanish Final: {show['name']}"
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


def test_vote_tile_only_highlights_unvoted_main_shows_containing_the_users_song(
    client, db, national_final, login
):
    @settings(max_examples=8, deadline=None)
    @given(has_main_song=st.booleans(), has_main_ballot=st.booleans())
    def property_test(has_main_song, has_main_ballot):
        owned_song_id = _add_candidate(db, national_final["id"], submitter=3)
        unrelated_song_id = _add_candidate(db, national_final["id"], submitter=2)
        with db.cursor() as cursor:
            main_show_id = cursor.execute(
                """INSERT INTO show (
                       year_id, point_system_id, show_type, status, voting_opens
                   ) VALUES (2025, %s, 'f', 'none', CURRENT_TIMESTAMP)
                   RETURNING id""",
                (national_final["point_system_id"],),
            ).fetchone()["id"]
            assignments = [
                (national_final["show_id"], owned_song_id, 1),
                (main_show_id, unrelated_song_id, 1),
            ]
            if has_main_song:
                assignments.append((main_show_id, owned_song_id, 2))
            cursor.executemany(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, %s)""",
                assignments,
            )
            cursor.execute(
                """UPDATE show
                   SET voting_opens = CURRENT_TIMESTAMP, voting_closes = NULL
                   WHERE id = %s""",
                (national_final["show_id"],),
            )
            main_vote_set_id = None
            if has_main_ballot:
                main_vote_set_id = cursor.execute(
                    """INSERT INTO vote_set (voter_id, show_id, country_id)
                       VALUES (3, %s, 'US') RETURNING id""",
                    (main_show_id,),
                ).fetchone()["id"]
        db.commit()
        try:
            client.delete_cookie("session")
            login(3)
            response = client.get("/", headers={"Accept": "application/json"})
            assert response.status_code == 200
            assert response.get_json()["has_pending_vote"] is (
                has_main_song and not has_main_ballot
            )
        finally:
            client.delete_cookie("session")
            if main_vote_set_id is not None:
                db.execute("DELETE FROM vote_set WHERE id = %s", (main_vote_set_id,))
                db.commit()
            _remove_candidates(db, [owned_song_id, unrelated_song_id])
            db.execute(
                "UPDATE show SET voting_opens = NULL, voting_closes = NULL WHERE id = %s",
                (national_final["show_id"],),
            )
            db.commit()
            _remove_shows(db, [main_show_id])

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


def test_member_national_final_view_contains_exactly_the_viewers_finals(
    client, db, national_final, login
):
    @settings(max_examples=8, deadline=None)
    @given(owners=st.lists(st.sampled_from([2, 3]), min_size=1, max_size=5))
    def property_test(owners):
        first_id = db.execute(
            "SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM national_final"
        ).fetchone()["id"]
        created_ids = [first_id + offset for offset in range(len(owners))]
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO national_final (
                       id, year_id, owner_id, short_name, name
                   ) VALUES (%s, 2025, %s, %s, %s)""",
                [
                    (nf_id, owner_id, f"owned-{nf_id}", f"Owned final {nf_id}")
                    for nf_id, owner_id in zip(created_ids, owners, strict=True)
                ],
            )
        db.commit()
        try:
            for viewer_id in (2, 3):
                client.delete_cookie("session")
                login(viewer_id)
                response = client.get(
                    "/member/national-finals",
                    headers={"Accept": "application/json"},
                )
                assert response.status_code == 200
                returned = response.get_json()["national_finals"]
                expected_ids = {
                    row["id"]
                    for row in db.execute(
                        "SELECT id FROM national_final WHERE owner_id = %s",
                        (viewer_id,),
                    ).fetchall()
                }
                assert {nf["id"] for nf in returned} == expected_ids
        finally:
            client.delete_cookie("session")
            db.execute("DELETE FROM national_final WHERE id = ANY(%s)", (created_ids,))
            db.commit()

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
                assert all(
                    row["entry_number"] >= 2
                    for row in db.execute(
                        "SELECT entry_number FROM song WHERE id = ANY(%s)", (created,)
                    ).fetchall()
                )
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


def test_finished_nf_orders_unassigned_candidates_last_without_placing_them(
    client, db, national_final
):
    @settings(max_examples=8, deadline=None)
    @given(candidate_count=st.integers(2, 5), assignment_seed=st.integers(0, 20))
    def property_test(candidate_count, assignment_seed):
        db.rollback()
        db.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        candidates = [
            _add_candidate(db, national_final["id"], submitter=3)
            for _ in range(candidate_count)
        ]
        assigned_count = 1 + assignment_seed % (candidate_count - 1)
        assigned = candidates[:assigned_count]
        unassigned = candidates[assigned_count:]
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, %s)""",
                [
                    (national_final["show_id"], song_id, position)
                    for position, song_id in enumerate(assigned, start=1)
                ],
            )
            cursor.execute(
                "UPDATE show SET status = 'full' WHERE id = %s",
                (national_final["show_id"],),
            )
            cursor.execute(
                "UPDATE national_final SET status = 'finished' WHERE id = %s",
                (national_final["id"],),
            )
        db.commit()
        try:
            response = client.get(
                "/year/2025/nfs/test-es", headers={"Accept": "application/json"}
            )
            assert response.status_code == 200
            payload = response.get_json()
            ordered_ids = [candidate["id"] for candidate in payload["candidates"]]
            result_ids = {int(song_id) for song_id in payload["results"]}
            places = {
                int(song_id): ranking["overall_place"]
                for song_id, ranking in payload["rankings"].items()
            }

            assert set(ordered_ids[: len(result_ids)]) == result_ids
            assert set(unassigned).isdisjoint(result_ids)
            assert set(ordered_ids[-len(unassigned) :]) == set(unassigned)
            assert set(places) == set(assigned)
            assert [places[song_id] for song_id in ordered_ids if song_id in places] == list(
                range(1, assigned_count + 1)
            )
        finally:
            _remove_candidates(db, candidates)
            db.execute(
                "UPDATE show SET status = 'none' WHERE id = %s",
                (national_final["show_id"],),
            )
            db.execute(
                "UPDATE national_final SET status = 'draft' WHERE id = %s",
                (national_final["id"],),
            )
            db.commit()

    property_test()


def test_nf_owner_can_open_breakdowns_while_previewing_unpublished_results(
    client, db, national_final, login
):
    @settings(max_examples=6, deadline=None)
    @given(status=st.sampled_from(["none", "draw"]))
    def property_test(status):
        db.rollback()
        db.execute(
            "INSERT INTO show_status (name) VALUES (%s) ON CONFLICT DO NOTHING",
            (status,),
        )
        song_id = _add_candidate(db, national_final["id"], submitter=3)
        candidate = db.execute("SELECT entry_number FROM song WHERE id = %s", (song_id,)).fetchone()
        db.execute(
            """INSERT INTO song_show (show_id, song_id, running_order)
               VALUES (%s, %s, 1)""",
            (national_final["show_id"], song_id),
        )
        db.execute(
            "UPDATE show SET status = %s WHERE id = %s",
            (status, national_final["show_id"]),
        )
        db.commit()
        try:
            login(2)
            response = client.get(
                f"/year/2025/test-es-f/song/es/{candidate['entry_number']}",
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 200
            assert response.get_json()["song"]["id"] == song_id

            client.delete_cookie("session")
            login(3)
            response = client.get(f"/year/2025/test-es-f/song/es/{candidate['entry_number']}")
            assert response.status_code == 400
        finally:
            client.delete_cookie("session")
            _remove_candidates(db, [song_id])
            db.execute(
                "UPDATE show SET status = 'none' WHERE id = %s",
                (national_final["show_id"],),
            )
            db.commit()

    property_test()


def test_nf_management_pages_are_available_only_to_staff_and_the_show_owner(
    client, db, national_final, login
):
    @settings(max_examples=12, deadline=None)
    @given(
        actor=st.sampled_from([None, 1, 2, 3]),
        page=st.sampled_from(["predictions", "penalty"]),
    )
    def property_test(actor, page):
        db.rollback()
        db.execute(
            """UPDATE show
               SET voting_ruleset_version = 'v5',
                   voting_closes = CURRENT_TIMESTAMP - INTERVAL '1 minute'
               WHERE id = %s""",
            (national_final["show_id"],),
        )
        song_id = _add_candidate(db, national_final["id"], submitter=3)
        db.execute(
            """INSERT INTO song_show (show_id, song_id, running_order)
               VALUES (%s, %s, 1)""",
            (national_final["show_id"], song_id),
        )
        db.commit()
        try:
            client.delete_cookie("session")
            if actor is not None:
                login(actor)

            response = client.get(f"/year/2025/test-es-f/{page}")
            if actor in (1, 2):
                assert response.status_code == 200
            elif page == "predictions":
                assert response.status_code == 400
            else:
                assert response.status_code == 403
        finally:
            client.delete_cookie("session")
            _remove_candidates(db, [song_id])

    property_test()


def test_verifications_include_only_the_selected_national_final_candidate(
    client, db, national_final, login
):
    login(1)

    @settings(max_examples=8, deadline=None)
    @given(
        candidate_count=st.integers(1, 5),
        selected_index=st.one_of(st.none(), st.integers(0, 20)),
        linked=st.booleans(),
        revised=st.booleans(),
    )
    def property_test(candidate_count, selected_index, linked, revised):
        candidates = [
            _add_candidate(db, national_final["id"], submitter=3)
            for _ in range(candidate_count)
        ]
        selected_id = (
            candidates[selected_index % candidate_count] if selected_index is not None else None
        )
        if not linked:
            db.execute(
                "DELETE FROM national_final_song WHERE song_id = ANY(%s)",
                (candidates,),
            )
        if revised:
            for song_id in candidates:
                db.execute(
                    """INSERT INTO song_data (song_id, title, artist_credit_set_id)
                       VALUES (%s, 'Replacement', test_artist_credit('Replacement Artist'))""",
                    (song_id,),
                )
        db.execute(
            "UPDATE song SET main_participant = true WHERE id = %s",
            (selected_id,),
        )
        db.commit()
        try:
            response = client.get(
                "/admin/manage/2025/verifications",
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 200
            visible_candidate_ids = {
                entry["song_id"]
                for group in response.get_json()["verification_groups"]
                for entry in ([group["song"]] if group["song"] else [])
                + group["historical_entries"]
                if entry["song_id"] in candidates
            }
            assert visible_candidate_ids == ({selected_id} if selected_id is not None else set())
        finally:
            _remove_candidates(db, candidates)

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


def test_aggregate_views_ignore_nfs_while_show_histories_keep_them_distinct(
    client, db, national_final
):
    scores = st.sampled_from([12, 10, 8])

    @settings(max_examples=8, deadline=None)
    @given(
        main_score=scores,
        nf_selected_score=scores,
        nf_only_score=scores,
        year_floor=st.integers(2023, 2027),
    )
    def property_test(main_score, nf_selected_score, nf_only_score, year_floor):
        db.rollback()
        db.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        selected_id = _add_candidate(db, national_final["id"], submitter=3)
        nf_only_id = _add_candidate(db, national_final["id"], submitter=3)
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE song SET main_participant = true WHERE id = %s",
                (selected_id,),
            )
            main_show_id = cursor.execute(
                """INSERT INTO show (
                       year_id, point_system_id, show_type, status
                   ) VALUES (2025, %s, 'f', 'full') RETURNING id""",
                (national_final["point_system_id"],),
            ).fetchone()["id"]
            cursor.executemany(
                """INSERT INTO song_show (show_id, song_id, running_order)
                   VALUES (%s, %s, %s)""",
                [
                    (national_final["show_id"], selected_id, 1),
                    (national_final["show_id"], nf_only_id, 2),
                    (main_show_id, selected_id, 1),
                ],
            )
            main_vote_set_id = cursor.execute(
                """INSERT INTO vote_set (voter_id, show_id, country_id)
                   VALUES (1, %s, 'US') RETURNING id""",
                (main_show_id,),
            ).fetchone()["id"]
            nf_vote_set_id = cursor.execute(
                """INSERT INTO vote_set (voter_id, show_id, country_id)
                   VALUES (1, %s, 'US') RETURNING id""",
                (national_final["show_id"],),
            ).fetchone()["id"]
            main_revote_set_id = cursor.execute(
                """INSERT INTO vote_set (
                       voter_id, show_id, country_id, result_mode
                   ) VALUES (1, %s, 'US', 'revote') RETURNING id""",
                (main_show_id,),
            ).fetchone()["id"]
            nf_revote_set_id = cursor.execute(
                """INSERT INTO vote_set (
                       voter_id, show_id, country_id, result_mode
                   ) VALUES (1, %s, 'US', 'revote') RETURNING id""",
                (national_final["show_id"],),
            ).fetchone()["id"]
            main_prediction_set_id = cursor.execute(
                """INSERT INTO prediction_set (user_id, show_id)
                   VALUES (1, %s) RETURNING id""",
                (main_show_id,),
            ).fetchone()["id"]
            nf_prediction_set_id = cursor.execute(
                """INSERT INTO prediction_set (user_id, show_id)
                   VALUES (1, %s) RETURNING id""",
                (national_final["show_id"],),
            ).fetchone()["id"]
            cursor.executemany(
                """INSERT INTO vote (vote_set_id, song_id, score)
                   VALUES (%s, %s, %s)""",
                [
                    (main_vote_set_id, selected_id, main_score),
                    (nf_vote_set_id, selected_id, nf_selected_score),
                    (nf_vote_set_id, nf_only_id, nf_only_score),
                ],
            )
            cursor.execute(
                "UPDATE show SET status = 'full' WHERE id = %s",
                (national_final["show_id"],),
            )
            cursor.execute(
                """UPDATE show SET date = CURRENT_TIMESTAMP
                   WHERE id = ANY(%s)""",
                ([main_show_id, national_final["show_id"]],),
            )
            cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2025")
        db.commit()
        try:
            selected_title = db.execute(
                "SELECT title FROM current_song WHERE id = %s", (selected_id,)
            ).fetchone()["title"]
            views = (
                ("country&country=ES", "regular_entries"),
                ("user&user=3", "regular_entries"),
                ("year&year=2025", "entries"),
            )
            for query, result_key in views:
                response = client.get(
                    f"/user/alice/votes?view={query}",
                    headers={"Accept": "application/json"},
                )
                assert response.status_code == 200
                entries = response.get_json()[result_key]
                assert [entry["title"] for entry in entries] == [selected_title]
                assert entries[0]["total"] == main_score
                assert entries[0]["final"]["pts"] == main_score

            default_votes = client.get(
                "/user/alice/votes", headers={"Accept": "application/json"}
            ).get_json()["votes"]
            assert {vote["show_id"] for vote in default_votes} == {main_show_id}

            bounded_votes = client.get(
                f"/user/alice/votes?from={year_floor}",
                headers={"Accept": "application/json"},
            ).get_json()["votes"]
            expected_show_ids = {main_show_id} if year_floor <= 2025 else set()
            assert {vote["show_id"] for vote in bounded_votes} == expected_show_ids

            previous_votes = client.get(
                "/user/alice/votes?edition=normal&edition=national-final",
                headers={"Accept": "application/json"},
            ).get_json()["votes"]
            assert {vote["show_id"] for vote in previous_votes} == {
                main_show_id,
                national_final["show_id"],
            }
            nf_vote = next(
                vote
                for vote in previous_votes
                if vote["show_id"] == national_final["show_id"]
            )
            assert nf_vote["short_name"] == "test-es-f"
            assert nf_vote["national_final_name"] == "Test Spanish Final"

            default_revotes = client.get(
                "/user/alice/revotes", headers={"Accept": "application/json"}
            ).get_json()["votes"]
            assert {vote["show_id"] for vote in default_revotes} == {main_show_id}

            previous_revotes = client.get(
                "/user/alice/revotes?edition=normal&edition=national-final",
                headers={"Accept": "application/json"},
            ).get_json()["votes"]
            assert {vote["show_id"] for vote in previous_revotes} == {
                main_show_id,
                national_final["show_id"],
            }

            previous_predictions = client.get(
                "/user/alice/predictions", headers={"Accept": "application/json"}
            ).get_json()["predictions"]
            assert {prediction["show_id"] for prediction in previous_predictions} == {
                main_show_id,
                national_final["show_id"],
            }

            voter_grid = client.get(
                "/year/2025/voters", headers={"Accept": "application/json"}
            ).get_json()
            assert voter_grid["voter_show_names"] == ["f"]

            revote_year = client.get(
                "/revote/2025", headers={"Accept": "application/json"}
            ).get_json()
            assert [show["short_name"] for show in revote_year["shows"]] == [
                "f",
                "test-es-f",
            ]
            assert revote_year["shows"][1]["name"] == "Test Spanish Final: Final"
        finally:
            db.execute(
                "DELETE FROM vote WHERE vote_set_id = ANY(%s)",
                ([main_vote_set_id, nf_vote_set_id],),
            )
            db.execute(
                "DELETE FROM vote_set WHERE id = ANY(%s)",
                (
                    [
                        main_vote_set_id,
                        nf_vote_set_id,
                        main_revote_set_id,
                        nf_revote_set_id,
                    ],
                ),
            )
            db.execute(
                "DELETE FROM prediction_set WHERE id = ANY(%s)",
                ([main_prediction_set_id, nf_prediction_set_id],),
            )
            db.commit()
            _remove_candidates(db, [selected_id, nf_only_id])
            db.execute(
                "UPDATE show SET status = 'none' WHERE id = %s",
                (national_final["show_id"],),
            )
            _remove_shows(db, [main_show_id])
            db.execute("UPDATE year SET status = 'open' WHERE id = 2025")
            db.commit()

    property_test()


def test_main_selection_reserves_number_one_for_the_selected_candidate(
    client, db, national_final, login
):
    login(2)

    @settings(max_examples=12, deadline=None)
    @given(
        candidate_count=st.integers(2, 5),
        initial_selection=st.one_of(st.none(), st.integers(0, 20)),
        actions=st.lists(st.tuples(st.integers(0, 20), st.booleans()), min_size=1, max_size=8),
    )
    def property_test(candidate_count, initial_selection, actions):
        candidates = [_add_candidate(db, national_final["id"]) for _ in range(candidate_count)]
        selected_id = (
            None if initial_selection is None else candidates[initial_selection % candidate_count]
        )
        try:
            db.execute("UPDATE song SET main_participant = true WHERE id = %s", (selected_id,))
            db.execute("SELECT normalize_national_final_entry_numbers(2025, 'ES')")
            db.commit()

            def check_numbers():
                rows = db.execute(
                    "SELECT id, entry_number FROM song WHERE id = ANY(%s)", (candidates,)
                ).fetchall()
                assert len({row["entry_number"] for row in rows}) == candidate_count
                for row in rows:
                    if row["id"] == selected_id:
                        assert row["entry_number"] == 1
                    else:
                        assert row["entry_number"] >= 2

            check_numbers()
            for index, enabled in actions:
                song_id = candidates[index % candidate_count]
                response = client.post(
                    "/year/2025/nfs/test-es/manage",
                    data={
                        "action": "set_main_participant",
                        "song_id": song_id,
                        "enabled": str(enabled).lower(),
                    },
                )
                assert response.status_code == 302
                if enabled:
                    selected_id = song_id
                elif selected_id == song_id:
                    selected_id = None
                check_numbers()
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


def test_show_media_stays_in_its_contest_management(client, db, national_final, login):
    login(1)
    main_show = db.execute(
        "INSERT INTO show (year_id, show_type) VALUES (2025, 'f') RETURNING id"
    ).fetchone()["id"]
    db.execute(
        """INSERT INTO year (id, special_name, special_short_name)
           VALUES (-2034, 'Media Special', 'nf-media-special') ON CONFLICT DO NOTHING"""
    )
    db.commit()

    @settings(max_examples=8, deadline=None)
    @given(
        special=st.booleans(),
        name=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
    )
    def check(special, name):
        year = -2034 if special else 2025
        db.execute(
            "UPDATE national_final SET year_id = %s WHERE id = %s", (year, national_final["id"])
        )
        db.execute("UPDATE show SET year_id = %s WHERE id IN (%s, %s)",
                   (year, main_show, national_final['show_id']))
        db.commit()
        year_url = '/admin/manage/special/nf-media-special' if special else '/admin/manage/2025'
        nf_url = (
            '/year/special/nf-media-special/nfs/test-es/manage' if special
            else '/year/2025/nfs/test-es/manage'
        )
        year_data = client.get(year_url, headers={'Accept': 'application/json'}).get_json()
        assert [show['id'] for show in year_data['shows']] == [main_show]
        nf_data = client.get(nf_url, headers={'Accept': 'application/json'}).get_json()
        assert [show['id'] for show in nf_data['shows']] == [national_final['show_id']]
        opening = f'https://media.world-stage.org/{name}.mov'
        response = client.post(
            f"/api/show/{'nf-media-special' if special else year}-test-es-f/metadata",
            data={'opening': opening, 'intervals': '', 'return_to': 'manage'},
        )
        assert response.status_code == 303
        assert response.location == nf_url
        assert client.get(nf_url, headers={'Accept': 'text/html'}).status_code == 200
        assert client.get(year_url, headers={'Accept': 'text/html'}).status_code == 200
        data = client.get(nf_url, headers={'Accept': 'application/json'}).get_json()
        assert data['shows'][0]['metadata'] == {'opening': opening, 'intervals': []}

    check()
