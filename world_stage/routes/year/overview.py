

from ...db import fetchone, get_db
from ...utils import (
    Show,
    UserPermissions,
    get_show_results_for_songs,
    get_year_placements,
    get_year_songs,
    get_year_winner,
    render_template,
    with_permissions,
)
from .common import bp, get_specials, resolve_special


@bp.get("/")
def index():
    db = get_db()
    cursor = db.cursor()

    years = []
    upcoming = []
    ongoing = []

    cursor.execute("SELECT id, status FROM year WHERE id >= 0 ORDER BY id DESC")
    for data in cursor.fetchall():
        if data["status"] == "closed":
            years.append(data)
        elif data["status"] == "ongoing":
            ongoing.append(data)
        else:
            upcoming.append(data)

    upcoming.reverse()

    for year in years:
        year["winner"] = get_year_winner(year["id"])

    specials = get_specials()

    return render_template(
        "year/index.html", years=years, upcoming=upcoming, specials=specials, ongoing=ongoing
    )


@bp.get("/special/<short_name>")
@with_permissions
def special(short_name: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    db = get_db()
    cursor = db.cursor()

    songs = get_year_songs(_year, select_languages=True)

    cursor.execute(
        "SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder", (_year,)
    )
    total_entries = fetchone(cursor)["c"]
    total_placeholders = len(songs) - total_entries
    cursor.execute(
        "SELECT short_name, show_name, date FROM show WHERE year_id = %s ORDER BY id", (_year,)
    )
    shows = [
        Show(year=_year, short_name=show["short_name"], name=show["show_name"], date=show["date"])
        for show in cursor.fetchall()
    ]
    shows.sort()

    cl = special_year["status"] == "closed"
    year_placements = get_year_placements(_year) if cl else {}

    show_names = {s.short_name for s in shows}
    has_sc = "sc" in show_names
    has_sf = any(sn == "sf" or sn.startswith("sf") for sn in show_names)
    multi_show = has_sc or has_sf

    results = get_show_results_for_songs([s.id for s in songs]) if (multi_show and cl) else {}

    sf_numbers: dict[int, str] = {}
    if has_sf:
        cursor.execute(
            """
            SELECT ss.song_id, sh.short_name
            FROM song_show ss
            JOIN show sh ON sh.id = ss.show_id
            WHERE sh.year_id = %s
              AND LEFT(sh.short_name, 2) = 'sf'
        """,
            (_year,),
        )
        sf_numbers = {row["song_id"]: row["short_name"] for row in cursor.fetchall()}

    if permissions.can_view_restricted:
        can_view_voters = True
    else:
        cursor.execute(
            "SELECT 1 FROM show "
            "WHERE year_id = %s AND status IN ('partial', 'full') LIMIT 1",
            (_year,),
        )
        can_view_voters = cursor.fetchone() is not None

    return render_template(
        "year/year.html",
        year=short_name,
        songs=songs,
        free_countries=[],
        is_closed=cl,
        is_open=special_year["status"] == "open",
        shows=shows,
        total=total_entries,
        placeholders=total_placeholders,
        year_placements=year_placements,
        results=results,
        multi_show=multi_show,
        has_sc=has_sc,
        has_sf=has_sf,
        sf_numbers=sf_numbers,
        can_view_voters=can_view_voters,
        special=short_name,
        special_name=special_year["special_name"],
    )

@bp.get("/<int:year>")
@with_permissions
def year(year: int, permissions: UserPermissions):
    _year = year
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT status FROM year WHERE id = %s", (_year,))
    year_row = cursor.fetchone() or {"status": "open"}
    cl = year_row["status"] == "closed"

    songs = get_year_songs(_year, select_languages=True)

    free_countries = []

    if year_row["status"] == "open" and _year >= 0:
        cursor.execute(
            """
            SELECT id, name FROM country
            WHERE id <> ALL(%(ccs)s)
              AND is_participating = true
              AND available_from <= %(year)s
              AND available_until >= %(year)s
            ORDER BY name
        """,
            {"ccs": [s.country.cc for s in songs], "year": _year},
        )

        free_countries = cursor.fetchall()

    cursor.execute(
        "SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder", (_year,)
    )
    total_entries = fetchone(cursor)["c"]
    total_placeholders = len(songs) - total_entries
    cursor.execute(
        "SELECT short_name, show_name, date FROM show WHERE year_id = %s ORDER BY id", (_year,)
    )
    shows = [
        Show(year=_year, short_name=show["short_name"], name=show["show_name"], date=show["date"])
        for show in cursor.fetchall()
    ]
    shows.sort()

    year_placements = get_year_placements(_year) if cl else {}

    show_names = {s.short_name for s in shows}
    has_sc = "sc" in show_names
    has_sf = any(sn == "sf" or sn.startswith("sf") for sn in show_names)
    multi_show = has_sc or has_sf

    results = get_show_results_for_songs([s.id for s in songs]) if (multi_show and cl == 1) else {}

    # SF assignment: which semi-final each song competed in, regardless of
    # whether the show is published yet (no status gate).
    sf_numbers: dict[int, str] = {}
    if has_sf:
        cursor.execute(
            """
            SELECT ss.song_id, sh.short_name
            FROM song_show ss
            JOIN show sh ON sh.id = ss.show_id
            WHERE sh.year_id = %s
              AND LEFT(sh.short_name, 2) = 'sf'
        """,
            (_year,),
        )
        sf_numbers = {row["song_id"]: row["short_name"] for row in cursor.fetchall()}

    if permissions.can_view_restricted:
        can_view_voters = True
    else:
        cursor.execute(
            "SELECT 1 FROM show "
            "WHERE year_id = %s AND status IN ('partial', 'full') LIMIT 1",
            (_year,),
        )
        can_view_voters = cursor.fetchone() is not None

    return render_template(
        "year/year.html",
        year=year,
        songs=songs,
        free_countries=free_countries,
        is_closed=cl,
        is_open=year_row["status"] == "open",
        shows=shows,
        total=total_entries,
        placeholders=total_placeholders,
        year_placements=year_placements,
        results=results,
        multi_show=multi_show,
        has_sc=has_sc,
        has_sf=has_sf,
        sf_numbers=sf_numbers,
        can_view_voters=can_view_voters,
    )
