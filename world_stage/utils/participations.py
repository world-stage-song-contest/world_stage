from typing import TypedDict

from ..db import fetchone, get_db


class DoubleEntryStats(TypedDict):
    double_entry_years: int
    participation_years: int
    score: float
    percentage: float | None


def get_double_entry_stats(user_id: int) -> DoubleEntryStats:
    cursor = get_db().cursor()
    cursor.execute(
        """
        WITH entry_years AS (
            SELECT song.year_id, COUNT(*) AS entry_count
            FROM current_song AS song
            WHERE song.submitter_id = %(user_id)s
              AND song.year_id >= 1995
              AND song.main_participant
              AND NOT song.is_placeholder
            GROUP BY song.year_id
        ),
        voting_years AS (
            SELECT show.year_id, COUNT(DISTINCT show.id) AS voted_shows
            FROM vote_set
            JOIN show ON show.id = vote_set.show_id
            WHERE vote_set.voter_id = %(user_id)s
              AND vote_set.result_mode = 'official'
              AND show.year_id >= 1995
              AND show.national_final_id IS NULL
            GROUP BY show.year_id
        ),
        show_years AS (
            SELECT show.year_id, COUNT(*) AS total_shows
            FROM show
            WHERE show.year_id >= 1995
              AND show.national_final_id IS NULL
            GROUP BY show.year_id
        ),
        participation_years AS (
            SELECT year_id FROM entry_years
            UNION
            SELECT year_id FROM voting_years
        )
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(entry_years.entry_count, 0) >= 2)
                AS double_entry_years,
            COUNT(*) AS participation_years,
            COALESCE(SUM(
                CASE
                    WHEN COALESCE(entry_years.entry_count, 0) >= 2 THEN 1
                    WHEN entry_years.entry_count = 1 THEN 0
                    ELSE -voting_years.voted_shows::numeric / show_years.total_shows
                END
            ), 0) AS score
        FROM participation_years
        LEFT JOIN entry_years USING (year_id)
        LEFT JOIN voting_years USING (year_id)
        LEFT JOIN show_years USING (year_id)
        """,
        {"user_id": user_id},
    )
    row = fetchone(cursor)
    double_entry_years = row["double_entry_years"]
    participation_years = row["participation_years"]
    score = float(row["score"])
    return {
        "double_entry_years": double_entry_years,
        "participation_years": participation_years,
        "score": score,
        "percentage": (
            100 * score / participation_years
            if participation_years
            else None
        ),
    }
