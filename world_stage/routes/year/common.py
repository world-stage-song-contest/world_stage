
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
        SELECT id, status, special_name, special_short_name
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
        SELECT id, status, special_name, special_short_name FROM year
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
        SELECT short_name FROM show
        WHERE year_id = %s AND short_name <> %s
        ORDER BY id
    """,
        (year, exclude_show),
    )

    return [row["short_name"] for row in cursor.fetchall()]
