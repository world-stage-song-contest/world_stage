"""Voting ruleset assignment and ballot-entry policy."""

from decimal import Decimal
from uuid import uuid4


def _create_show(cursor, *, version: str | None, scores: list[int]) -> int:
    cursor.execute(
        "INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING"
    )
    # Other tests seed reference point systems with explicit IDs, so choose an
    # explicit unused ID rather than depending on their identity sequence.
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS id FROM point_system")
    point_system_id = cursor.fetchone()["id"]
    cursor.execute(
        "INSERT INTO point_system (id, number) VALUES (%s, %s)",
        (point_system_id, len(scores)),
    )
    cursor.executemany(
        """
        INSERT INTO point (id, point_system_id, place, score)
        VALUES (%s, %s, %s, %s)
        """,
        [
            (point_system_id * 100 + place, point_system_id, place, score)
            for place, score in enumerate(scores, start=1)
        ],
    )
    cursor.execute(
        """
        INSERT INTO show (
            year_id, point_system_id, voting_ruleset_version,
            show_name, short_name, status
        )
        VALUES (2025, %s, %s, %s, %s, 'none')
        RETURNING id
        """,
        (
            point_system_id,
            version,
            f"Ruleset test {point_system_id}",
            f"rules-{point_system_id}",
        ),
    )
    return cursor.fetchone()["id"]


def _add_entry(
    cursor,
    *,
    show_id: int,
    country_id: str,
    submitter_id: int,
    position: int,
) -> int:
    cursor.execute(
        """
        INSERT INTO song (country_id, year_id, entry_number)
        VALUES (%s, 2025, %s)
        RETURNING id
        """,
        (
            country_id,
            show_id * 10 + position,
        ),
    )
    song_id = cursor.fetchone()["id"]
    cursor.execute(
        """INSERT INTO song_data (
               song_id, title, artist, submitter_id
           ) VALUES (%s, %s, 'Artist', %s)""",
        (song_id, f"Entry {show_id}-{position}", submitter_id),
    )
    cursor.execute(
        """
        INSERT INTO song_show (song_id, show_id, running_order)
        VALUES (%s, %s, %s)
        """,
        (song_id, show_id, position),
    )
    return song_id


def _rule(
    cursor,
    *,
    show_id: int,
    result_mode: str,
    voter_id: int,
    country_id: str,
    song_id: int,
) -> dict:
    cursor.execute(
        """
        SELECT rule_kind, required_score, score_cap
        FROM ballot_entry_rule(%s, %s, %s, %s, %s)
        """,
        (show_id, result_mode, voter_id, country_id, song_id),
    )
    return cursor.fetchone()


def test_new_show_snapshots_current_ruleset(db):
    with db.cursor() as cursor:
        show_id = _create_show(cursor, version=None, scores=[12, 10, 8])
        cursor.execute(
            """
            SELECT voting_ruleset_version, revote_ruleset_version
            FROM show
            WHERE id = %s
            """,
            (show_id,),
        )
        assert cursor.fetchone() == {
            "voting_ruleset_version": "v5",
            "revote_ruleset_version": "v6",
        }


def test_v5_and_v6_penalize_non_voters(db):
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT version, penalizes_non_voters
            FROM voting_ruleset
            ORDER BY version
            """
        )
        assert [
            (row["version"], row["penalizes_non_voters"])
            for row in cursor.fetchall()
        ] == [
            ("v1", False),
            ("v2", False),
            ("v3", False),
            ("v4", False),
            ("v5", True),
            ("v6", True),
        ]


def test_v1_forces_flag_entry_but_allows_other_owned_entries(db):
    with db.cursor() as cursor:
        show_id = _create_show(cursor, version="v1", scores=[20, 10, 1])
        flag_entry = _add_entry(
            cursor,
            show_id=show_id,
            country_id="US",
            submitter_id=1,
            position=1,
        )
        other_owned_entry = _add_entry(
            cursor,
            show_id=show_id,
            country_id="ES",
            submitter_id=1,
            position=2,
        )

        assert _rule(
            cursor,
            show_id=show_id,
            result_mode="official",
            voter_id=1,
            country_id="US",
            song_id=flag_entry,
        ) == {"rule_kind": "FORCED", "required_score": 1, "score_cap": 1}
        assert _rule(
            cursor,
            show_id=show_id,
            result_mode="official",
            voter_id=1,
            country_id="US",
            song_id=other_owned_entry,
        ) == {"rule_kind": "NORMAL", "required_score": None, "score_cap": 20}


def test_v2_and_v3_distinguish_flag_and_ownership_rules(db):
    with db.cursor() as cursor:
        v2_show = _create_show(cursor, version="v2", scores=[12, 10, 1])
        v2_flag_entry = _add_entry(
            cursor,
            show_id=v2_show,
            country_id="US",
            submitter_id=2,
            position=1,
        )
        v2_owned_entry = _add_entry(
            cursor,
            show_id=v2_show,
            country_id="ES",
            submitter_id=1,
            position=2,
        )

        assert _rule(
            cursor,
            show_id=v2_show,
            result_mode="official",
            voter_id=1,
            country_id="US",
            song_id=v2_flag_entry,
        )["rule_kind"] == "FORBIDDEN"
        assert _rule(
            cursor,
            show_id=v2_show,
            result_mode="official",
            voter_id=1,
            country_id="US",
            song_id=v2_owned_entry,
        )["rule_kind"] == "NORMAL"

        v3_show = _create_show(cursor, version="v3", scores=[12, 10, 1])
        v3_flag_entry = _add_entry(
            cursor,
            show_id=v3_show,
            country_id="US",
            submitter_id=2,
            position=1,
        )
        v3_owned_entry = _add_entry(
            cursor,
            show_id=v3_show,
            country_id="ES",
            submitter_id=1,
            position=2,
        )
        v3_overlap_entry = _add_entry(
            cursor,
            show_id=v3_show,
            country_id="FR",
            submitter_id=1,
            position=3,
        )

        assert _rule(
            cursor,
            show_id=v3_show,
            result_mode="official",
            voter_id=1,
            country_id="US",
            song_id=v3_flag_entry,
        )["rule_kind"] == "FORBIDDEN"
        assert _rule(
            cursor,
            show_id=v3_show,
            result_mode="official",
            voter_id=1,
            country_id="US",
            song_id=v3_owned_entry,
        )["rule_kind"] == "FORBIDDEN"
        assert _rule(
            cursor,
            show_id=v3_show,
            result_mode="official",
            voter_id=1,
            country_id="FR",
            song_id=v3_overlap_entry,
        ) == {"rule_kind": "FORBIDDEN", "required_score": None, "score_cap": 0}


def test_v4_and_v5_forbid_owned_entries_only(db):
    with db.cursor() as cursor:
        for version in ("v4", "v5"):
            show_id = _create_show(cursor, version=version, scores=[12, 10, 1])
            owned_entry = _add_entry(
                cursor,
                show_id=show_id,
                country_id="US",
                submitter_id=1,
                position=1,
            )
            other_entry = _add_entry(
                cursor,
                show_id=show_id,
                country_id="ES",
                submitter_id=2,
                position=2,
            )

            assert _rule(
                cursor,
                show_id=show_id,
                result_mode="official",
                voter_id=1,
                country_id="ES",
                song_id=owned_entry,
            )["rule_kind"] == "FORBIDDEN"
            assert _rule(
                cursor,
                show_id=show_id,
                result_mode="official",
                voter_id=1,
                country_id="ES",
                song_id=other_entry,
            ) == {"rule_kind": "NORMAL", "required_score": None, "score_cap": 12}


def test_revote_capacity_exception_uses_point_system_size(db):
    with db.cursor() as cursor:
        show_id = _create_show(cursor, version="v1", scores=[20, 10, 1])
        owned_entry = _add_entry(
            cursor,
            show_id=show_id,
            country_id="US",
            submitter_id=1,
            position=1,
        )
        _add_entry(
            cursor,
            show_id=show_id,
            country_id="ES",
            submitter_id=1,
            position=2,
        )
        _add_entry(
            cursor,
            show_id=show_id,
            country_id="FR",
            submitter_id=2,
            position=3,
        )
        _add_entry(
            cursor,
            show_id=show_id,
            country_id="ES",
            submitter_id=3,
            position=4,
        )

        # Two non-owned entries cannot fill three scored positions, so all
        # entries become eligible under the revote exception.
        assert _rule(
            cursor,
            show_id=show_id,
            result_mode="revote",
            voter_id=1,
            country_id="US",
            song_id=owned_entry,
        ) == {"rule_kind": "NORMAL", "required_score": None, "score_cap": 20}

        _add_entry(
            cursor,
            show_id=show_id,
            country_id="FR",
            submitter_id=3,
            position=5,
        )

        # At exactly three non-owned entries the ballot can be completed
        # without the exception.
        assert _rule(
            cursor,
            show_id=show_id,
            result_mode="revote",
            voter_id=1,
            country_id="US",
            song_id=owned_entry,
        ) == {"rule_kind": "FORBIDDEN", "required_score": None, "score_cap": 0}


def test_adjusted_percentage_formula_uses_midpoint_on_both_branches(db):
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                calculate_adjusted_points_percentage(29, 58, 100) AS below,
                calculate_adjusted_points_percentage(58, 58, 100) AS midpoint,
                calculate_adjusted_points_percentage(79, 58, 100) AS above,
                calculate_adjusted_points_percentage(0, 0, 0) AS empty
            """
        )
        assert cursor.fetchone() == {
            "below": Decimal("25"),
            "midpoint": Decimal("50"),
            "above": Decimal("75.0"),
            "empty": Decimal("0"),
        }


def test_results_store_dynamic_adjusted_metrics_and_keep_legacy_percentage(db):
    with db.cursor() as cursor:
        show_id = _create_show(cursor, version="v1", scores=[20, 10, 1])
        songs = [
            _add_entry(
                cursor,
                show_id=show_id,
                country_id=country_id,
                submitter_id=submitter_id,
                position=position,
            )
            for position, (country_id, submitter_id) in enumerate(
                (("US", 1), ("ES", 2), ("FR", 3)),
                start=1,
            )
        ]
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (1, %s, 'US', 'official')
            RETURNING id
            """,
            (show_id,),
        )
        vote_set_id = cursor.fetchone()["id"]
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [
                (vote_set_id, songs[0], 1),
                (vote_set_id, songs[1], 20),
                (vote_set_id, songs[2], 10),
            ],
        )

        cursor.execute(
            "SELECT refresh_show_results_for_mode(%s, 'official')",
            (show_id,),
        )
        cursor.execute(
            """
            SELECT
                country_id,
                total_points,
                max_possible_points,
                points_percentage,
                adjusted_max_possible_points,
                points_midpoint,
                adjusted_points_percentage
            FROM country_show_results
            WHERE show_id = %s AND result_mode = 'official'
            ORDER BY country_id
            """,
            (show_id,),
        )

        assert cursor.fetchall() == [
            {
                "country_id": "ES",
                "total_points": 20,
                "max_possible_points": 20,
                "points_percentage": Decimal("100.00"),
                "adjusted_max_possible_points": 20,
                "points_midpoint": Decimal("10.333333"),
                "adjusted_points_percentage": Decimal("100.00"),
            },
            {
                "country_id": "FR",
                "total_points": 10,
                "max_possible_points": 20,
                "points_percentage": Decimal("50.00"),
                "adjusted_max_possible_points": 20,
                "points_midpoint": Decimal("10.333333"),
                "adjusted_points_percentage": Decimal("48.39"),
            },
            {
                "country_id": "US",
                "total_points": 1,
                "max_possible_points": 20,
                "points_percentage": Decimal("5.00"),
                "adjusted_max_possible_points": 1,
                "points_midpoint": Decimal("10.333333"),
                "adjusted_points_percentage": Decimal("4.84"),
            },
        ]


def test_official_penalty_policy_comes_from_snapshotted_ruleset(client, db):
    with db.cursor() as cursor:
        shows = {
            version: _create_show(cursor, version=version, scores=[12])
            for version in ("v4", "v5")
        }
        for show_id in shows.values():
            song_id = _add_entry(
                cursor,
                show_id=show_id,
                country_id="US",
                submitter_id=1,
                position=1,
            )
            cursor.execute(
                """
                INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
                VALUES (2, %s, 'ES', 'official')
                RETURNING id
                """,
                (show_id,),
            )
            vote_set_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, 12)",
                (vote_set_id, song_id),
            )
            cursor.execute(
                "UPDATE song_show SET penalty = 12 WHERE show_id = %s",
                (show_id,),
            )
            cursor.execute(
                "SELECT refresh_show_results_for_mode(%s, 'official')",
                (show_id,),
            )

        cursor.execute(
            """
            SELECT
                sh.voting_ruleset_version AS version,
                voting_ruleset_penalizes_non_voters(
                    sh.id, 'official'
                ) AS penalties_enabled,
                csr.total_points
            FROM show sh
            JOIN country_show_results csr ON csr.show_id = sh.id
            WHERE sh.id = ANY(%s) AND csr.result_mode = 'official'
            ORDER BY sh.voting_ruleset_version
            """,
            (list(shows.values()),),
        )
        assert cursor.fetchall() == [
            {"version": "v4", "penalties_enabled": False, "total_points": 12},
            {"version": "v5", "penalties_enabled": True, "total_points": 0},
        ]

        session_id = uuid4()
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (1, %s, CURRENT_TIMESTAMP + INTERVAL '1 hour')
            """,
            (session_id,),
        )
        cursor.execute("SELECT short_name FROM show WHERE id = %s", (shows["v4"],))
        v4_short_name = cursor.fetchone()["short_name"]
    db.commit()
    client.set_cookie("session", str(session_id))

    response = client.post(
        f"/year/2025/{v4_short_name}/penalty",
        json={"song_ids": []},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "This show's voting ruleset does not apply non-voter penalties."
    )
