from ..db import fetchone, get_db
from .types import Country, ShowData


def get_show_id(show: str, year: int | None = None) -> ShowData | None:
    db = get_db()
    cursor = db.cursor()

    if year:
        short_show_name = show
    else:
        # Format: "year-show" e.g. "2025-f" or "cs24-f" for specials
        parts = show.split("-", 1)
        if len(parts) == 2:
            short_show_name = parts[1]
            try:
                year = int(parts[0])
            except ValueError:
                # Non-numeric prefix: look up as special short name
                cursor.execute("SELECT id FROM year WHERE special_short_name = %s", (parts[0],))
                row = cursor.fetchone()
                if not row:
                    return None
                year = row["id"]
        else:
            return None

    cursor.execute(
        """
        SELECT id, year_id, point_system_id, show_name, voting_opens, voting_closes,
               predictions_close, dtf, sc, special, status
        FROM show
        WHERE year_id = %s AND short_name = %s
    """,
        (year, short_show_name),
    )

    show_row = cursor.fetchone()
    if show_row:
        show_id = show_row["id"]
        year_id = show_row["year_id"]
        point_system_id = show_row["point_system_id"]
        show_name = show_row["show_name"]
        voting_opens = show_row["voting_opens"]
        voting_closes = show_row["voting_closes"]
        predictions_close = show_row["predictions_close"]
        dtf = show_row["dtf"]
        sc = show_row["sc"]
        special = show_row["special"]
        status = show_row["status"]
    else:
        return None

    points = get_points_for_system(point_system_id)

    ret = ShowData(
        id=show_id,
        points=list(points),
        point_system_id=point_system_id,
        name=show_name,
        short_name=short_show_name,
        voting_opens=voting_opens,
        voting_closes=voting_closes,
        predictions_close=predictions_close,
        year=year_id,
        dtf=dtf,
        sc=sc,
        special=special,
        status=status,
    )

    return ret


def get_points_for_system(point_system_id: int) -> list[int]:
    db = get_db()
    cursor = db.cursor()

    points = []
    cursor.execute(
        """
        SELECT score FROM point
        WHERE point_system_id = %s
        ORDER BY place
    """,
        (point_system_id,),
    )
    for p in cursor.fetchall():
        points.append(p["score"])

    return points


def get_countries(only_participating: bool = False) -> list[Country]:
    if only_participating:
        query = """
            SELECT id, name, is_participating, cc3 FROM country
            WHERE is_participating AND id <> 'XX' ORDER BY name"""
    else:
        query = "SELECT id, name, is_participating, cc3 FROM country WHERE id <> 'XX' ORDER BY name"
    db = get_db()
    cursor = db.cursor()

    cursor.execute(query)
    countries = [
        Country(
            cc=row["id"],
            name=row["name"],
            is_participating=bool(row["is_participating"]),
            cc3=row["cc3"],
        )
        for row in cursor.fetchall()
    ]
    return countries


def get_year_placements(year: int) -> dict[int, int]:
    """Return {song_id: place} from country_year_results for a closed year."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song_id, place FROM country_year_results WHERE year_id = %s
    """,
        (year,),
    )
    return {row["song_id"]: row["place"] for row in cursor.fetchall()}


def get_years() -> list[int]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT id FROM year
    """)
    return list(map(lambda x: x["id"], cursor.fetchall()))


def get_closed_years() -> list[int]:
    """Return closed positive year ids in ascending order, for range pickers."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT id FROM year
        WHERE id > 0 AND status = 'closed'
        ORDER BY id
    """)
    return [row["id"] for row in cursor]


def get_years_grouped() -> dict:
    """Return years split into groups for display in submission forms:
    - open: status = 'open', ascending
    - closed: status <> 'open', ascending
    - specials: negative IDs with their special_name / special_short_name
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT id, status, special_name, special_short_name
        FROM year
        ORDER BY id
    """)
    open_years: list[int] = []
    closed_years: list[int] = []
    specials: list[dict] = []
    for row in cursor.fetchall():
        if row["id"] < 0:
            specials.append(
                {
                    "id": row["id"],
                    "special_name": row["special_name"],
                    "special_short_name": row["special_short_name"],
                }
            )
        elif row["status"] == "open":
            open_years.append(row["id"])
        else:
            closed_years.append(row["id"])
    return {"open": open_years, "closed": closed_years, "specials": specials}


def get_year_countries(
    year: int, *, sort_by_priority: bool = False, host: bool = True
) -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    order_by = "priority" if sort_by_priority else "country.name"

    add = ""
    if not host:
        add = "AND country.id <> year.host_id"

    cursor.execute(
        f"""
        SELECT country.id AS cc, country.name, country.pot, song.submitter_id AS submitter FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON song.year_id = year.id {add}
        WHERE song.year_id = %s
        ORDER BY {order_by}
    """,
        (year,),
    )
    countries = cursor.fetchall()

    return countries


def get_year_shows(year: int, pattern: str = "") -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT show_name, short_name FROM show
        WHERE year_id = %s AND short_name LIKE %s COLLATE "C"
        ORDER BY short_name COLLATE "C"
    """,
        (year, pattern + "%"),
    )

    shows = cursor.fetchall()

    return shows


def get_vote_count_for_show(show_id: int) -> int:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT COUNT(*) AS c FROM vote_set
        WHERE show_id = %s
    """,
        (show_id,),
    )
    count = fetchone(cursor)["c"]
    return count


def resolve_country_code(code: str) -> str | None:
    """Resolve a country code (cc2 or cc3) to the canonical cc2 id. Returns None if not found."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id FROM country
        WHERE id = %(cc)s OR cc3 = %(cc)s
    """,
        {"cc": code},
    )
    row = cursor.fetchone()
    return row["id"] if row else None


def get_country_name(country_id: str) -> str:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT name FROM country
        WHERE id = %(cc)s OR cc3 = %(cc)s
    """,
        {"cc": country_id},
    )
    country_name = cursor.fetchone()
    if country_name:
        return country_name["name"]
    return "Unknown"
