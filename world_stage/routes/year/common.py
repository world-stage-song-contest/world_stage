
from flask import Blueprint

from ...db import get_db
from ...utils import (
    get_year_winner,
)

bp = Blueprint("year", __name__, url_prefix="/year")


def get_specials() -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id, status, submissions_open, special_name, special_short_name
        FROM year
        WHERE id < 0
        ORDER BY id DESC
    """)
    specials = [row for row in cursor.fetchall()]

    for special in specials:
        special["winner"] = get_year_winner(special["id"])

    return specials


def resolve_special(short_name: str) -> dict | None:
    """Look up a special by its short name. Returns the year row or None."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, status, submissions_open, special_name, special_short_name FROM year
        WHERE special_short_name = %s
        """,
        (short_name,),
    )
    return cursor.fetchone()

def get_other_shows(year: int, exclude_show: str | None) -> list[str]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT target.national_final_id
        FROM show AS target
        LEFT JOIN national_final AS nf ON nf.id = target.national_final_id
        WHERE target.year_id = %s
          AND (
              (target.national_final_id IS NULL AND target.short_name = %s)
              OR nf.short_name || '-' || target.short_name = %s
          )
        """,
        (year, exclude_show, exclude_show),
    )
    target = cursor.fetchone()
    national_final_id = target["national_final_id"] if target else None

    if national_final_id is None:
        cursor.execute(
            """
            SELECT short_name FROM show
            JOIN show_types ON show_types.id = show.show_type
            WHERE year_id = %s AND national_final_id IS NULL
              AND short_name <> %s
            ORDER BY show_types.sort_order, show.show_number NULLS FIRST, show.id
            """,
            (year, exclude_show),
        )
    else:
        cursor.execute(
            """
            SELECT nf.short_name || '-' || show.short_name AS short_name
            FROM show
            JOIN show_types ON show_types.id = show.show_type
            JOIN national_final AS nf ON nf.id = show.national_final_id
            WHERE show.national_final_id = %s
              AND nf.short_name || '-' || show.short_name <> %s
            ORDER BY show_types.sort_order, show.show_number NULLS FIRST, show.id
            """,
            (national_final_id, exclude_show),
        )

    return [row["short_name"] for row in cursor.fetchall()]
