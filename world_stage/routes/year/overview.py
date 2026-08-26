

from flask import redirect, request, url_for

from ...db import fetchone, get_db
from ...utils import (
    Show,
    Song,
    UserPermissions,
    get_show_results_for_songs,
    get_year_index_winners,
    get_year_overview_songs,
    get_year_placements,
    render_template,
    require_user,
    with_permissions,
)
from .common import bp, resolve_special


def _ongoing_national_finals(year_id: int) -> list[dict]:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT national_final.short_name, national_final.name,
               country.id AS country_id, country.name AS country_name,
               account.username AS owner_username
        FROM national_final
        JOIN country ON country.id = national_final.owner_country_id
        JOIN account ON account.id = national_final.owner_id
        WHERE national_final.year_id = %s
          AND national_final.status IN ('draft', 'submissions', 'voting')
        ORDER BY country.name
        """,
        (year_id,),
    )
    return cursor.fetchall()


def _overview_rows(
    songs: list[Song], national_finals: list[dict], *, is_closed: bool
) -> list[dict]:
    rows = [
        {"song": song, "national_final": None, "country_name": song.country.name}
        for song in songs
    ]
    rows.extend(
        {
            "song": None,
            "national_final": national_final,
            "country_name": national_final["country_name"],
        }
        for national_final in national_finals
    )
    if not is_closed:
        rows.sort(key=lambda row: row["country_name"].casefold())
    return rows


@bp.post("/<int(signed=True):year_id>/spot-watch")
@require_user()
def update_spot_watch(year_id: int, user: tuple[int, str]):
    country_id = request.form.get("country_id", "").strip().upper()
    entry_number = request.form.get("entry_number", type=int)
    action = request.form.get("action")
    if not country_id or entry_number is None or action not in {"watch", "unwatch"}:
        return render_template("error.html", error="Invalid spot watch request"), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT special_short_name, submissions_open FROM year WHERE id = %s",
        (year_id,),
    )
    year_row = cursor.fetchone()
    if year_row is None:
        return render_template("error.html", error="Year not found"), 404

    if action == "watch":
        if not year_row["submissions_open"]:
            return (
                render_template(
                    "error.html",
                    error="Spot watches are only available while submissions are open",
                ),
                403,
            )
        cursor.execute(
            """
            INSERT INTO year_spot_watch (
                account_id, year_id, country_id, entry_number
            )
            SELECT %s, year_id, country_id, COALESCE(entry_number, 1)
            FROM current_song
            WHERE year_id = %s
              AND country_id = %s
              AND COALESCE(entry_number, 1) = %s
              AND main_participant
            ON CONFLICT (account_id, year_id, country_id, entry_number)
            DO UPDATE SET created_at = year_spot_watch.created_at
            RETURNING account_id
            """,
            (user[0], year_id, country_id, entry_number),
        )
        if cursor.fetchone() is None:
            db.rollback()
            return render_template("error.html", error="Spot not found"), 404
    else:
        cursor.execute(
            """
            DELETE FROM year_spot_watch
            WHERE account_id = %s
              AND year_id = %s
              AND country_id = %s
              AND entry_number = %s
            """,
            (user[0], year_id, country_id, entry_number),
        )
    db.commit()

    if year_row["special_short_name"]:
        return redirect(
            url_for(
                "country.special_details",
                code=country_id.lower(),
                special_short_name=year_row["special_short_name"],
                entry_number=entry_number,
            )
        )
    return redirect(
        url_for(
            "country.details",
            code=country_id.lower(),
            year=year_id,
            entry_number=entry_number,
        )
    )


@bp.get("/")
def index():
    db = get_db()
    cursor = db.cursor()

    years = []
    upcoming = []

    cursor.execute(
        """
        SELECT id, status, submissions_open, special_name, special_short_name
        FROM year
        ORDER BY id DESC
        """
    )
    specials = []
    for data in cursor.fetchall():
        if data["id"] < 0:
            specials.append(data)
        elif data["status"] in {"closed", "ongoing"}:
            years.append(data)
        else:
            upcoming.append(data)

    upcoming.reverse()

    ongoing_years = [year for year in years if year["status"] == "ongoing"]
    for year in ongoing_years:
        year["shows"] = []
    if ongoing_years:
        cursor.execute(
            """
            SELECT show.year_id, show.short_name, show.show_name
            FROM show
            JOIN show_types ON show_types.id = show.show_type
            WHERE show.year_id = ANY(%s)
              AND show.national_final_id IS NULL
            ORDER BY show.year_id DESC, show_types.sort_order,
                     show.show_number NULLS FIRST, show.id
            """,
            ([year["id"] for year in ongoing_years],),
        )
        shows_by_year = {year["id"]: year["shows"] for year in ongoing_years}
        for show in cursor.fetchall():
            shows_by_year[show["year_id"]].append(show)

    winners = get_year_index_winners()
    for item in (*years, *specials):
        item["winner"] = winners.get(item["id"])

    return render_template(
        "year/index.html", years=years, upcoming=upcoming, specials=specials
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

    songs = get_year_overview_songs(_year)
    ongoing_national_finals = _ongoing_national_finals(_year)

    cursor.execute(
        """SELECT COUNT(*) AS c FROM current_song AS song
           WHERE year_id = %s AND main_participant AND NOT is_placeholder""",
        (_year,),
    )
    total_entries = fetchone(cursor)["c"]
    total_placeholders = len(songs) - total_entries
    total_entries += len(ongoing_national_finals)
    cursor.execute(
        "SELECT show.short_name, show.show_name, show.date FROM show "
        "JOIN show_types ON show_types.id = show.show_type "
        "WHERE year_id = %s AND national_final_id IS NULL "
        "ORDER BY show_types.sort_order, show.show_number NULLS FIRST, show.id",
        (_year,),
    )
    shows = [
        Show(year=_year, short_name=show["short_name"], name=show["show_name"], date=show["date"])
        for show in cursor.fetchall()
    ]

    cl = special_year["status"] == "closed"
    year_placements = get_year_placements(_year) if cl else {}

    show_names = {s.short_name for s in shows}
    has_f = "f" in show_names
    has_sc = "sc" in show_names
    has_sf = any(sn == "sf" or sn.startswith("sf") for sn in show_names)
    has_result_stages = has_f or has_sc or has_sf

    results = (
        get_show_results_for_songs([s.id for s in songs]) if (has_result_stages and cl) else {}
    )

    sf_numbers: dict[int, str] = {}
    if has_sf:
        cursor.execute(
            """
            SELECT ss.song_id, sh.short_name
            FROM song_show ss
            JOIN show sh ON sh.id = ss.show_id
            WHERE sh.year_id = %s
              AND sh.national_final_id IS NULL
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
            "WHERE year_id = %s AND national_final_id IS NULL "
            "AND status IN ('partial', 'full') LIMIT 1",
            (_year,),
        )
        can_view_voters = cursor.fetchone() is not None

    return render_template(
        "year/year.html",
        year=short_name,
        songs=songs,
        overview_rows=_overview_rows(songs, ongoing_national_finals, is_closed=cl),
        free_countries=[],
        is_closed=cl,
        submissions_open=special_year["submissions_open"],
        shows=shows,
        total=total_entries,
        placeholders=total_placeholders,
        year_placements=year_placements,
        results=results,
        has_result_stages=has_result_stages,
        has_f=has_f,
        has_sc=has_sc,
        has_sf=has_sf,
        sf_numbers=sf_numbers,
        can_view_voters=can_view_voters,
        special=short_name,
        special_name=special_year["special_name"],
        ongoing_national_finals=ongoing_national_finals,
        can_view_verifications=permissions.can_moderate,
    )

@bp.get("/<int:year>")
@with_permissions
def year(year: int, permissions: UserPermissions):
    _year = year
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT status, submissions_open FROM year WHERE id = %s", (_year,))
    year_row = cursor.fetchone() or {"status": "open", "submissions_open": False}
    cl = year_row["status"] == "closed"

    songs = get_year_overview_songs(_year)
    ongoing_national_finals = _ongoing_national_finals(_year)

    free_countries = []

    if year_row["submissions_open"] and _year >= 0:
        cursor.execute(
            """
            SELECT id, name FROM country
            WHERE id <> ALL(%(ccs)s)
              AND is_participating = true
              AND available_from <= %(year)s
              AND available_until >= %(year)s
              AND NOT EXISTS (
                  SELECT 1 FROM national_final
                  WHERE national_final.year_id = %(year)s
                    AND national_final.owner_country_id = country.id
                    AND national_final.status <> 'cancelled'
              )
            ORDER BY name
        """,
            {"ccs": [s.country.cc for s in songs], "year": _year},
        )

        free_countries = cursor.fetchall()

    cursor.execute(
        """SELECT COUNT(*) AS c FROM current_song AS song
           WHERE year_id = %s AND main_participant AND NOT is_placeholder""",
        (_year,),
    )
    total_entries = fetchone(cursor)["c"]
    total_placeholders = len(songs) - total_entries
    total_entries += len(ongoing_national_finals)
    cursor.execute(
        "SELECT show.short_name, show.show_name, show.date FROM show "
        "JOIN show_types ON show_types.id = show.show_type "
        "WHERE year_id = %s AND national_final_id IS NULL "
        "ORDER BY show_types.sort_order, show.show_number NULLS FIRST, show.id",
        (_year,),
    )
    shows = [
        Show(year=_year, short_name=show["short_name"], name=show["show_name"], date=show["date"])
        for show in cursor.fetchall()
    ]

    year_placements = get_year_placements(_year) if cl else {}

    show_names = {s.short_name for s in shows}
    has_f = "f" in show_names
    has_sc = "sc" in show_names
    has_sf = any(sn == "sf" or sn.startswith("sf") for sn in show_names)
    has_result_stages = has_f or has_sc or has_sf

    results = (
        get_show_results_for_songs([s.id for s in songs]) if (has_result_stages and cl) else {}
    )

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
              AND sh.national_final_id IS NULL
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
            "WHERE year_id = %s AND national_final_id IS NULL "
            "AND status IN ('partial', 'full') LIMIT 1",
            (_year,),
        )
        can_view_voters = cursor.fetchone() is not None

    return render_template(
        "year/year.html",
        year=year,
        songs=songs,
        overview_rows=_overview_rows(songs, ongoing_national_finals, is_closed=cl),
        free_countries=free_countries,
        is_closed=cl,
        submissions_open=year_row["submissions_open"],
        shows=shows,
        total=total_entries,
        placeholders=total_placeholders,
        year_placements=year_placements,
        results=results,
        has_result_stages=has_result_stages,
        has_f=has_f,
        has_sc=has_sc,
        has_sf=has_sf,
        sf_numbers=sf_numbers,
        can_view_voters=can_view_voters,
        ongoing_national_finals=ongoing_national_finals,
        can_view_verifications=permissions.can_moderate,
    )
