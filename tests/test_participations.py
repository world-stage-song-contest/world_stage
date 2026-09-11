import pytest
from hypothesis import given
from hypothesis import strategies as st

from world_stage.db import get_db
from world_stage.utils import get_double_entry_stats


def _add_entry(
    cursor,
    year: int,
    country: str,
    *,
    placeholder: bool = False,
    main_participant: bool = True,
) -> int:
    cursor.execute(
        """
        INSERT INTO song (country_id, year_id, main_participant)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (country, year, main_participant),
    )
    song_id = cursor.fetchone()["id"]
    cursor.execute(
        """
        INSERT INTO song_data (
            song_id, submitter_id, title, artist_credit_set_id
        ) VALUES (%s, 2, %s, test_artist_credit(%s))
        RETURNING id
        """,
        (song_id, f"Entry {year} {country}", f"Artist {year} {country}"),
    )
    song_data_id = cursor.fetchone()["id"]
    if placeholder:
        cursor.execute(
            """
            INSERT INTO song_status (song_id, song_data_id, is_placeholder)
            VALUES (%s, %s, true)
            """,
            (song_id, song_data_id),
        )
    return song_id


def _add_show(
    cursor,
    year: int,
    *,
    voted: bool = True,
    show_number: int | None = None,
    national_final_id: int | None = None,
) -> None:
    cursor.execute(
        """
        INSERT INTO show (
            year_id, voting_ruleset_version, revote_ruleset_version,
            show_type, show_number, status, national_final_id
        ) VALUES (
            %s, 'v5', 'v6',
            CASE WHEN %s::smallint IS NULL THEN 'f' ELSE 'sf' END,
            %s, 'full', %s
        )
        RETURNING id
        """,
        (year, show_number, show_number, national_final_id),
    )
    show_id = cursor.fetchone()["id"]
    if voted:
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, result_mode)
            VALUES (2, %s, 'official')
            """,
            (show_id,),
        )


@st.composite
def _year_activity(draw):
    entry_count = draw(st.integers(min_value=0, max_value=2))
    total_shows = draw(st.integers(min_value=1, max_value=4))
    voted_shows = draw(st.integers(min_value=0, max_value=total_shows))
    return entry_count, voted_shows, total_shows


def test_double_entry_percentage_counts_distinct_regular_participation_years(app):
    @given(
        activity=st.lists(
            _year_activity(),
            min_size=0,
            max_size=6,
        )
    )
    def property_test(activity):
        with app.app_context():
            cursor = get_db().cursor()

            cursor.execute("INSERT INTO year (id, status, host_id) VALUES (1994, 'closed', 'US')")
            _add_entry(cursor, 1994, "US")
            _add_entry(cursor, 1994, "ES")
            _add_show(cursor, 1994)

            cursor.execute(
                """
                INSERT INTO year (
                    id, status, host_id, special_name, special_short_name
                ) VALUES (-999, 'closed', 'US', 'Test special', 'test-special')
                """,
            )
            _add_entry(cursor, -999, "US")
            _add_entry(cursor, -999, "ES")
            _add_show(cursor, -999)

            cursor.execute("INSERT INTO year (id, status, host_id) VALUES (2199, 'closed', 'US')")
            cursor.execute(
                """
                INSERT INTO national_final (
                    year_id, owner_id, owner_country_id, short_name, name
                ) VALUES (2199, 2, 'US', 'test-final', 'Test final')
                RETURNING id
                """
            )
            national_final_id = cursor.fetchone()["id"]
            for country in ("US", "ES"):
                song_id = _add_entry(cursor, 2199, country, main_participant=False)
                cursor.execute(
                    """
                    INSERT INTO national_final_song (national_final_id, song_id)
                    VALUES (%s, %s)
                    """,
                    (national_final_id, song_id),
                )
            _add_show(cursor, 2199, national_final_id=national_final_id)

            for offset, (entry_count, voted_shows, total_shows) in enumerate(activity):
                year = 2100 + offset
                cursor.execute(
                    "INSERT INTO year (id, status, host_id) VALUES (%s, 'closed', 'US')",
                    (year,),
                )
                for country in ("US", "ES")[:entry_count]:
                    _add_entry(cursor, year, country)
                _add_entry(cursor, year, "FR", placeholder=True)

                for show_number in range(1, total_shows + 1):
                    _add_show(
                        cursor,
                        year,
                        voted=show_number <= voted_shows,
                        show_number=show_number,
                    )

            result = get_double_entry_stats(2)
            participation_years = sum(
                entries > 0 or voted_shows > 0
                for entries, voted_shows, _total_shows in activity
            )
            double_entry_years = sum(
                entries == 2 for entries, _voted_shows, _total_shows in activity
            )
            score = sum(
                1
                if entries == 2
                else 0
                if entries == 1
                else -voted_shows / total_shows
                for entries, voted_shows, total_shows in activity
                if entries > 0 or voted_shows > 0
            )

            assert result["participation_years"] == participation_years
            assert result["double_entry_years"] == double_entry_years
            assert result["score"] == pytest.approx(score)
            if not participation_years:
                assert result["percentage"] is None
            else:
                assert result["percentage"] == pytest.approx(
                    100 * score / participation_years
                )

    property_test()
