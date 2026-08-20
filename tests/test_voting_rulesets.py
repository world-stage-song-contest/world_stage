"""Property tests for voting-rule assignment and ballot policy."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _create_show(cursor, *, version: str | None, scores: list[int]) -> int:
    cursor.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
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
        "SELECT COALESCE(MAX(show_number), 0) + 1 AS number "
        "FROM show WHERE year_id = 2025 AND show_type = 'sf'"
    )
    show_number = cursor.fetchone()["number"]
    cursor.execute(
        """
        INSERT INTO show (
            year_id, point_system_id, voting_ruleset_version,
            show_type, show_number, status
        )
        VALUES (2025, %s, %s, 'sf', %s, 'none')
        RETURNING id
        """,
        (point_system_id, version, show_number),
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
        (country_id, show_id * 10 + position),
    )
    song_id = cursor.fetchone()["id"]
    cursor.execute(
        """INSERT INTO song_data (
               song_id, title, artist_credit_set_id, submitter_id
           ) VALUES (%s, %s, test_artist_credit('Artist'), %s)""",
        (song_id, f"Entry {show_id}-{position}", submitter_id),
    )
    cursor.execute(
        "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
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
        SELECT rule_kind, rule_reason, required_score, score_cap
        FROM ballot_entry_rule(%s, %s, %s, %s, %s)
        """,
        (show_id, result_mode, voter_id, country_id, song_id),
    )
    return cursor.fetchone()


def _official_rule_model(version, *, flag, owned, max_score):
    if version == "v1" and flag:
        return {
            "rule_kind": "FORCED",
            "rule_reason": "flag",
            "required_score": 1,
            "score_cap": 1,
        }
    forbidden_reason = None
    if version == "v2" and flag:
        forbidden_reason = "flag"
    elif version == "v3" and (flag or owned):
        forbidden_reason = "flag_and_owner" if flag and owned else "flag" if flag else "owner"
    elif version in {"v4", "v5"} and owned:
        forbidden_reason = "owner"
    if forbidden_reason is not None:
        return {
            "rule_kind": "FORBIDDEN",
            "rule_reason": forbidden_reason,
            "required_score": None,
            "score_cap": 0,
        }
    return {
        "rule_kind": "NORMAL",
        "rule_reason": None,
        "required_score": None,
        "score_cap": max_score,
    }


def _adjusted_percentage(points: Decimal, midpoint: Decimal, maximum: Decimal) -> Decimal:
    if midpoint <= 0:
        return Decimal(0)
    if points <= midpoint:
        return Decimal(50) * points / midpoint
    return Decimal(50) + Decimal(50) * (points - midpoint) / (maximum - midpoint)


def test_new_shows_snapshot_whichever_rulesets_are_current(db, isolated_example):
    @settings(max_examples=20, deadline=None)
    @given(
        scores=st.lists(
            st.integers(min_value=1, max_value=100),
            min_size=1,
            max_size=8,
            unique=True,
        )
    )
    def property_test(scores):
        with isolated_example(), db.cursor() as cursor:
            cursor.execute(
                """SELECT
                       MAX(version) FILTER (WHERE is_current) AS official,
                       MAX(version) FILTER (WHERE is_current_revote) AS revote
                   FROM voting_ruleset"""
            )
            current = cursor.fetchone()
            show_id = _create_show(cursor, version=None, scores=scores)
            cursor.execute(
                """SELECT voting_ruleset_version AS official,
                          revote_ruleset_version AS revote
                   FROM show WHERE id = %s""",
                (show_id,),
            )
            assert cursor.fetchone() == current

    property_test()


def test_official_entry_rules_and_rule_matrix_match_the_reference_model(db, isolated_example):
    @settings(max_examples=50, deadline=None)
    @given(
        version=st.sampled_from(["v1", "v2", "v3", "v4", "v5"]),
        flag=st.booleans(),
        owned=st.booleans(),
        max_score=st.integers(min_value=2, max_value=100),
    )
    def property_test(version, flag, owned, max_score):
        with isolated_example(), db.cursor() as cursor:
            show_id = _create_show(cursor, version=version, scores=[max_score, 1])
            song_id = _add_entry(
                cursor,
                show_id=show_id,
                country_id="US" if flag else "ES",
                submitter_id=1 if owned else 2,
                position=1,
            )
            cursor.execute(
                """INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
                   VALUES (1, %s, 'US', 'official') RETURNING id""",
                (show_id,),
            )
            vote_set_id = cursor.fetchone()["id"]

            scalar = _rule(
                cursor,
                show_id=show_id,
                result_mode="official",
                voter_id=1,
                country_id="US",
                song_id=song_id,
            )
            assert scalar == _official_rule_model(
                version,
                flag=flag,
                owned=owned,
                max_score=max_score,
            )

            cursor.execute(
                """SELECT rule_kind, rule_reason, required_score, score_cap
                   FROM ballot_entry_rule_matrix(%s, 'official')
                   WHERE vote_set_id = %s AND song_id = %s""",
                (show_id, vote_set_id, song_id),
            )
            assert cursor.fetchone() == scalar

    property_test()


def test_revote_ownership_exception_depends_only_on_ballot_capacity(db, isolated_example):
    @settings(max_examples=40, deadline=None)
    @given(
        scored_positions=st.integers(min_value=1, max_value=6),
        owned_count=st.integers(min_value=1, max_value=5),
        other_count=st.integers(min_value=0, max_value=7),
    )
    def property_test(scored_positions, owned_count, other_count):
        with isolated_example(), db.cursor() as cursor:
            scores = list(range(scored_positions, 0, -1))
            show_id = _create_show(cursor, version="v5", scores=scores)
            owned_song = _add_entry(
                cursor,
                show_id=show_id,
                country_id="US",
                submitter_id=1,
                position=1,
            )
            position = 2
            for _ in range(owned_count - 1):
                _add_entry(
                    cursor,
                    show_id=show_id,
                    country_id="ES",
                    submitter_id=1,
                    position=position,
                )
                position += 1
            for index in range(other_count):
                _add_entry(
                    cursor,
                    show_id=show_id,
                    country_id="FR" if index % 2 else "ES",
                    submitter_id=2,
                    position=position,
                )
                position += 1

            rule = _rule(
                cursor,
                show_id=show_id,
                result_mode="revote",
                voter_id=1,
                country_id="US",
                song_id=owned_song,
            )
            if other_count < scored_positions:
                assert rule["rule_kind"] == "NORMAL"
                assert rule["score_cap"] == scored_positions
            else:
                assert rule["rule_kind"] == "FORBIDDEN"
                assert rule["score_cap"] == 0

    property_test()


@st.composite
def adjusted_percentage_cases(draw):
    midpoint = draw(st.integers(min_value=1, max_value=10_000))
    maximum = midpoint + draw(st.integers(min_value=1, max_value=10_000))
    points = draw(st.integers(min_value=0, max_value=maximum))
    return points, midpoint, maximum


def test_adjusted_percentage_matches_the_piecewise_reference_formula(db, isolated_example):
    @settings(max_examples=80, deadline=None)
    @given(case=adjusted_percentage_cases())
    def property_test(case):
        points, midpoint, maximum = case
        with isolated_example(), db.cursor() as cursor:
            cursor.execute(
                "SELECT calculate_adjusted_points_percentage(%s, %s, %s) AS result",
                (points, midpoint, maximum),
            )
            actual = cursor.fetchone()["result"]
            expected = _adjusted_percentage(Decimal(points), Decimal(midpoint), Decimal(maximum))
            assert float(actual) == pytest.approx(float(expected))

    property_test()


def test_result_refresh_derives_metrics_from_scores_and_entry_rules(db, isolated_example):
    score_sets = st.lists(
        st.integers(min_value=2, max_value=100),
        min_size=2,
        max_size=2,
        unique=True,
    ).map(lambda values: sorted(values, reverse=True) + [1])

    @settings(max_examples=30, deadline=None)
    @given(scores=score_sets, swap_other_scores=st.booleans())
    def property_test(scores, swap_other_scores):
        with isolated_example(), db.cursor() as cursor:
            show_id = _create_show(cursor, version="v1", scores=scores)
            songs = {
                country: _add_entry(
                    cursor,
                    show_id=show_id,
                    country_id=country,
                    submitter_id=submitter,
                    position=position,
                )
                for position, (country, submitter) in enumerate(
                    (("US", 1), ("ES", 2), ("FR", 3)),
                    start=1,
                )
            }
            other_scores = scores[:2]
            if swap_other_scores:
                other_scores.reverse()
            awarded = {"US": 1, "ES": other_scores[0], "FR": other_scores[1]}
            cursor.execute(
                """INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
                   VALUES (1, %s, 'US', 'official') RETURNING id""",
                (show_id,),
            )
            vote_set_id = cursor.fetchone()["id"]
            cursor.executemany(
                "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
                [(vote_set_id, songs[country], score) for country, score in awarded.items()],
            )
            cursor.execute("SELECT refresh_show_results_for_mode(%s, 'official')", (show_id,))
            cursor.execute(
                """SELECT country_id, total_points, max_possible_points,
                          points_percentage, adjusted_max_possible_points,
                          points_midpoint, adjusted_points_percentage
                   FROM country_show_results
                   WHERE show_id = %s AND result_mode = 'official'""",
                (show_id,),
            )
            results = {row["country_id"]: row for row in cursor.fetchall()}

            midpoint = sum(Decimal(score) for score in scores) / len(scores)
            for country, score in awarded.items():
                legacy_maximum = Decimal(scores[0])
                adjusted_maximum = Decimal(1 if country == "US" else scores[0])
                result = results[country]
                assert result["total_points"] == score
                assert result["max_possible_points"] == legacy_maximum
                assert result["adjusted_max_possible_points"] == adjusted_maximum
                assert result["points_midpoint"] == pytest.approx(midpoint)
                assert float(result["points_percentage"]) == pytest.approx(
                    float(Decimal(100) * score / legacy_maximum),
                    abs=0.01,
                )
                assert float(result["adjusted_points_percentage"]) == pytest.approx(
                    float(_adjusted_percentage(Decimal(score), midpoint, adjusted_maximum)),
                    abs=0.01,
                )

    property_test()
