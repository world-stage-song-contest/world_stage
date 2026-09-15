import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from typing import Literal, overload

from flask import Blueprint, request, url_for

from .. import listens
from ..db import fetchone, get_db
from ..utils import (
    DoubleEntryStats,
    Song,
    UserPermissions,
    get_closed_years,
    get_countries,
    get_double_entry_stats,
    get_show_results_for_songs,
    get_user_submission_history,
    render_template,
    with_auth,
)
from .country import _country_stats, _format_decimal
from .member import playlists_for_user

bp = Blueprint("user", __name__, url_prefix="/user")

VOTE_HISTORY_PAGE_SIZE = 24
VOTE_HISTORY_EDITIONS = ("normal", "special", "national-final")
VOTE_HISTORY_DEFAULT_EDITIONS = ("normal", "special")
VOTE_HISTORY_ROUNDS = ("sf", "sc", "f")
VOTE_HISTORY_STATUSES = ("full", "partial")
LISTEN_HISTORY_PAGE_SIZE = 50


def _vote_history_filters() -> tuple[
    tuple[str, ...], tuple[str, ...], str, list[list[str]]
]:
    edition_filtering = "filters" in request.args or "edition" in request.args
    round_filtering = "filters" in request.args or "round" in request.args
    editions = tuple(
        value
        for value in VOTE_HISTORY_EDITIONS
        if (
            value in request.args.getlist("edition")
            if edition_filtering
            else value in VOTE_HISTORY_DEFAULT_EDITIONS
        )
    )
    rounds = tuple(
        value
        for value in VOTE_HISTORY_ROUNDS
        if not round_filtering or value in request.args.getlist("round")
    )

    edition_conditions = []
    if "normal" in editions:
        edition_conditions.append("(show.national_final_id IS NULL AND show.year_id >= 0)")
    if "special" in editions:
        edition_conditions.append("(show.national_final_id IS NULL AND show.year_id < 0)")
    if "national-final" in editions:
        edition_conditions.append("show.national_final_id IS NOT NULL")

    conditions = [
        f"({' OR '.join(edition_conditions)})" if edition_conditions else "FALSE",
        "show.show_type = ANY(%s)" if rounds else "FALSE",
    ]
    parameters = [list(rounds)] if rounds else []
    return editions, rounds, " AND ".join(conditions), parameters


def _vote_history_statuses() -> tuple[str, ...]:
    filtering = "filters" in request.args or "status" in request.args
    return tuple(
        value
        for value in VOTE_HISTORY_STATUSES
        if not filtering or value in request.args.getlist("status")
    )


def _vote_history_year_filter() -> tuple[int | None, int | None, str, list[int]]:
    year_from = request.args.get("from", type=int)
    year_to = request.args.get("to", type=int)
    bounds = []
    parameters = []
    if year_from is not None:
        bounds.append("show.year_id >= %s")
        parameters.append(year_from)
    if year_to is not None:
        bounds.append("show.year_id <= %s")
        parameters.append(year_to)
    sql = f"(show.year_id > 0 AND {' AND '.join(bounds)})" if bounds else "TRUE"
    return year_from, year_to, sql, parameters


def _vote_history_years(cursor) -> list[int]:
    cursor.execute(
        """
        SELECT id FROM year
        WHERE id > 0 AND status IN ('closed', 'ongoing')
        ORDER BY id
        """
    )
    return [row["id"] for row in cursor]


def _vote_history_pagination(total: int, endpoint: str, username: str) -> dict:
    pages = max(1, (total + VOTE_HISTORY_PAGE_SIZE - 1) // VOTE_HISTORY_PAGE_SIZE)
    page = min(max(request.args.get("page", type=int) or 1, 1), pages)

    def page_url(target: int) -> str:
        values = request.args.to_dict(flat=False)
        if target == 1:
            values.pop("page", None)
        else:
            values["page"] = [str(target)]
        return url_for(endpoint, username=username, **values)

    start = (page - 1) * VOTE_HISTORY_PAGE_SIZE
    page_links = [
        {"number": number, "url": page_url(number)}
        for number in range(1, pages + 1)
    ]

    return {
        "page": page,
        "pages": pages,
        "offset": start,
        "result_start": start + 1 if total else 0,
        "result_end": min(start + VOTE_HISTORY_PAGE_SIZE, total),
        "total_votes": total,
        "previous_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < pages else None,
        "page_links": page_links,
    }


@bp.get("/")
def index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id, username, role FROM account
        ORDER BY account.username
    """)
    users: defaultdict[str, list[dict]] = defaultdict(list)
    users["Admin"] = []
    for row in cursor.fetchall():
        if row["role"] == "admin" or row["role"] == "owner":
            users["Admin"].append({"id": row["id"], "username": row["username"]})
        first_letter = row["username"][0].upper()
        val = {"id": row["id"], "username": row["username"]}
        users[first_letter].append(val)

    return render_template("user/index.html", users=users)


@bp.get("/<username>")
def profile(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, username FROM account WHERE LOWER(username) = LOWER(%s)",
        (username,),
    )
    account = cursor.fetchone()
    if not account:
        return render_template("error.html", error="User not found"), 404

    return render_template(
        "user/page.html",
        username=account["username"],
    )


@bp.get("/<username>/playlist")
def playlists(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, username FROM account WHERE LOWER(username) = LOWER(%s)",
        (username,),
    )
    account = cursor.fetchone()
    if not account:
        return render_template("error.html", error="User not found"), 404

    return render_template(
        "user/playlists.html",
        username=account["username"],
        playlists=playlists_for_user(account["id"]),
    )


def _listen_history_account(username: str) -> dict | None:
    username = unicodedata.normalize("NFKC", urllib.parse.unquote(username))
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, username FROM account WHERE LOWER(username) = LOWER(%s)",
        (username,),
    )
    return cursor.fetchone()


@bp.get("/<username>/listen-history")
def listen_history(username: str):
    account = _listen_history_account(username)
    if not account:
        return render_template("error.html", error="User not found"), 404

    cursor = get_db().cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM song_play WHERE user_id = %s", (account["id"],))
    total = fetchone(cursor)["total"]
    pages = max(1, (total + LISTEN_HISTORY_PAGE_SIZE - 1) // LISTEN_HISTORY_PAGE_SIZE)
    page = min(max(request.args.get("page", type=int) or 1, 1), pages)
    cursor.execute(
        """
        SELECT play.*
        FROM song_play AS play
        WHERE play.user_id = %s
        ORDER BY play.played_at DESC, play.id DESC
        LIMIT %s OFFSET %s
        """,
        (account["id"], LISTEN_HISTORY_PAGE_SIZE, (page - 1) * LISTEN_HISTORY_PAGE_SIZE),
    )
    plays = cursor.fetchall()
    listens.prepare_history(plays)

    return render_template(
        "user/listen_history.html",
        username=account["username"],
        plays=plays,
        page=page,
        pages=pages,
        previous_url=(
            url_for("user.listen_history", username=account["username"], page=page - 1)
            if page > 1 else None
        ),
        next_url=(
            url_for("user.listen_history", username=account["username"], page=page + 1)
            if page < pages else None
        ),
    )


@bp.get("/<username>/listen-history/summary")
def listen_history_summary(username: str):
    account = _listen_history_account(username)
    if not account:
        return render_template("error.html", error="User not found"), 404

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song_id, title, artist, media_url, country_code, country_name,
               year_id, year_label,
               COUNT(*) AS listen_count, MAX(played_at) AS last_played_at
        FROM song_play
        WHERE user_id = %s
        GROUP BY song_id, title, artist, media_url, country_code, country_name,
                 year_id, year_label
        ORDER BY listen_count DESC, last_played_at DESC, song_id,
                 title, artist, media_url, country_code, country_name,
                 year_id, year_label
        """,
        (account["id"],),
    )
    songs = cursor.fetchall()
    listens.prepare_history(songs)
    return render_template(
        "user/listen_history_summary.html",
        username=account["username"],
        songs=songs,
    )


def _most_frequent_submission_countries(songs: list[Song]) -> list[dict]:
    counted_statuses = {"closed", "ongoing"}
    country_names = {country.cc: country.name for country in get_countries()}
    counts = Counter(
        song.country.cc
        for song in songs
        if song.year.status in counted_statuses
    )
    if not counts:
        return []

    top_count = max(counts.values())
    return [
        {"name": country_names.get(code, code), "count": count}
        for code, count in sorted(
            counts.items(),
            key=lambda item: country_names.get(item[0], item[0]),
        )
        if count == top_count
    ]


def _user_submission_stats(
    songs: list[Song],
    results: dict[int, dict],
    *,
    special: bool = False,
    ten_year_window: set[int] | None = None,
    double_entry: DoubleEntryStats | None = None,
) -> dict:
    stats = _country_stats(
        songs,
        results,
        special=special,
        ten_year_window=ten_year_window,
    )
    stats["most_frequent_countries"] = _most_frequent_submission_countries(songs)
    stats["double_entry"] = double_entry
    return stats


# Map a show's short_name to the aggregated column its points belong in.
# 'f' → Final, 'sc' → Repechage, 'sf'/'sf1'/'sf2'… → Semi.
def _vote_column(short_name: str) -> str | None:
    sn = (short_name or "").lower()
    if sn == "f":
        return "final"
    if sn == "sc":
        return "repe"
    if sn.startswith("sf"):
        return "semi"
    return None


def _aggregate_entries(rows, voter_id: int) -> list[dict]:
    """Group raw per-(show, entry) rows into one dict per (year, entry).

    Each show column records whether the entry actually competed in that show
    (``part``) — so the template can grey out shows a country sat out — plus
    the points the user awarded there (``pts``; None when none are shown).
    ``total`` and ``max_possible`` only count shows the user voted in, so the
    percentage reflects every opportunity the user had to back the entry.
    Kept as long as the user voted in at least one of the entry's shows, even
    if they awarded it no points there.
    """
    groups: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = (row["year_id"], row["song_id"])
        g = groups.get(key)
        if g is None:
            g = {
                "year_id": row["year_id"],
                "special_name": row["special_name"],
                "special_short_name": row["special_short_name"],
                "entry_number": row["entry_number"],
                "cc": row["cc"],
                "country": row["country"],
                "artist": row["artist"],
                "title": row["title"],
                "submitted_by_voter": row["submitter_id"] == voter_id,
                "total": 0,
                "max_possible": 0,
                "final": {"part": False, "pts": None, "voted_for": False},
                "repe": {"part": False, "pts": None, "voted_for": False},
                "semi": {"part": False, "pts": None, "voted_for": False},
            }
            groups[key] = g

        voted = row["vote_set_id"] is not None
        score = row["score"]
        if voted:
            g["max_possible"] += row["show_max"] or 0
        if score is not None:
            g["total"] += score

        col = _vote_column(row["short_name"])
        if col is not None:
            cell = g[col]
            cell["part"] = True
            # If the user cast a ballot in this show, record their score —
            # explicitly 0 when they awarded this entry nothing. Cells stay
            # blank only for shows the entry sat out or the user didn't vote in.
            if voted:
                cell["pts"] = (cell["pts"] or 0) + (score or 0)
                cell["voted_for"] = cell["voted_for"] or score is not None

    entries = [g for g in groups.values() if g["max_possible"] > 0]
    for g in entries:
        g["pct"] = (g["total"] / g["max_possible"] * 100) if g["max_possible"] else 0.0
    return entries


def _fetch_entries(cursor, voter_id: int, where_sql: str, where_val, *, revote=False) -> list[dict]:
    """``voter_id``'s vote totals over the entries selected by ``where_sql``,
    oldest year first.

    Driven off ``song_show`` (every show the entry actually competed in) and
    left-joined to the voter's votes, so the per-show columns can distinguish a
    show the entry sat out from one it competed in but earned no points. Only
    fully-revealed ('full') shows are counted, so partial-result shows can't
    leak which entries qualified. ``where_sql`` is a trusted constant (never
    user input); the matching value is always parameterised.
    """
    vote_set_join = """
        LEFT JOIN LATERAL (
            SELECT id FROM vote_set
            WHERE show_id = sh.id AND voter_id = %s
            ORDER BY (result_mode = 'revote') DESC
            LIMIT 1
        ) vote_set ON TRUE
    """ if revote else """
        LEFT JOIN vote_set ON vote_set.show_id = sh.id AND vote_set.voter_id = %s
                          AND vote_set.result_mode = 'official'
    """
    eligible = "AND sh.revote_eligible_at IS NOT NULL" if revote else ""
    cursor.execute(
        f"""
        SELECT sh.short_name, sh.year_id,
               year.special_name, year.special_short_name,
               song.id AS song_id, song.title, song.artist, song.entry_number,
               song.submitter_id,
               country.id AS cc, country.name AS country,
               vote_set.id AS vote_set_id, vote.score AS score,
               (SELECT MAX(point.score) FROM point
                WHERE point.point_system_id = sh.point_system_id) AS show_max
        FROM current_song AS song
        JOIN song_show ON song_show.song_id = song.id
        JOIN show sh ON song_show.show_id = sh.id
                    AND sh.status = 'full'
                    AND sh.national_final_id IS NULL
        JOIN country ON song.country_id = country.id
        LEFT JOIN year ON sh.year_id = year.id
        {vote_set_join}
        LEFT JOIN vote ON vote.vote_set_id = vote_set.id AND vote.song_id = song.id
        WHERE {where_sql} {eligible}
    """,
        (voter_id, where_val),
    )
    entries = _aggregate_entries(cursor.fetchall(), voter_id)
    entries.sort(key=lambda g: (g["year_id"] or 0, g["country"] or ""))
    return entries


def _country_entries(cursor, voter_id: int, code: str, *, revote=False) -> list[dict]:
    """``voter_id``'s vote totals for one country's entries."""
    return _fetch_entries(cursor, voter_id, "song.country_id = %s", code, revote=revote)


def _submitter_entries(cursor, voter_id: int, submitter_id: int, *, revote=False) -> list[dict]:
    """``voter_id``'s vote totals for entries submitted by ``submitter_id``."""
    return _fetch_entries(cursor, voter_id, "song.submitter_id = %s", submitter_id, revote=revote)


def _year_entries(cursor, voter_id: int, year_id: int, *, revote=False) -> list[dict]:
    """``voter_id``'s vote totals for every entry in one year (by country)."""
    return _fetch_entries(cursor, voter_id, "song.year_id = %s", year_id, revote=revote)


def _votes_by_country(cursor, user_id: int, username: str, *, revote=False):
    """Per-country view: a dropdown of countries that competed in shows where
    the user cast a ballot; picking one lists that country's entries with the
    user's points. Regular-year entries (oldest first) and special editions
    (by name) are split into two tables.
    """
    result_mode_filter = "" if revote else "AND history_vote_set.result_mode = 'official'"
    revote_filter = "AND sh.revote_eligible_at IS NOT NULL" if revote else ""
    cursor.execute(
        f"""
        SELECT DISTINCT country.id AS cc, country.name
        FROM country
        JOIN current_song AS song ON song.country_id = country.id
        JOIN song_show ON song_show.song_id = song.id
        JOIN show sh ON sh.id = song_show.show_id
                    AND sh.status = 'full'
                    AND sh.national_final_id IS NULL
        JOIN vote_set history_vote_set
          ON history_vote_set.show_id = sh.id
         AND history_vote_set.voter_id = %s
         {result_mode_filter}
        WHERE TRUE {revote_filter}
        ORDER BY country.name
    """,
        (user_id,),
    )
    country_list = [dict(r) for r in cursor.fetchall()]

    selected_code = request.args.get("country")
    selected_country = None
    regular_entries: list[dict] = []
    special_entries: list[dict] = []
    if selected_code:
        selected_country = next(
            (c for c in country_list if c["cc"].lower() == selected_code.lower()), None
        )
        if selected_country:
            entries = _country_entries(cursor, user_id, selected_country["cc"], revote=revote)
            # Specials use negative year ids; split them into their own table.
            regular_entries = [e for e in entries if (e["year_id"] or 0) >= 0]
            special_entries = [e for e in entries if (e["year_id"] or 0) < 0]
            special_entries.sort(key=lambda e: e["special_name"] or "")

    return render_template(
        "user/votes.html",
        username=username,
        view="country",
        country_list=country_list,
        selected_country=selected_country,
        regular_entries=regular_entries,
        special_entries=special_entries,
        is_revote=revote,
        history_endpoint="user.revotes" if revote else "user.votes",
    )


def _votes_by_user(cursor, user_id: int, username: str, *, revote=False):
    """Per-submitter view: a dropdown of other users whose entries competed in
    shows where this user cast a ballot; picking one lists those entries with
    this user's points. Like the per-country view but spanning countries, so
    the tables also carry a country column.
    """
    result_mode_filter = "" if revote else "AND history_vote_set.result_mode = 'official'"
    revote_filter = "AND sh.revote_eligible_at IS NOT NULL" if revote else ""
    cursor.execute(
        f"""
        SELECT DISTINCT account.id, account.username,
               LOWER(unaccent(account.username)) AS username_sort
        FROM account
        JOIN current_song AS song ON song.submitter_id = account.id
        JOIN song_show ON song_show.song_id = song.id
        JOIN show sh ON sh.id = song_show.show_id
                    AND sh.status = 'full'
                    AND sh.national_final_id IS NULL
        JOIN vote_set history_vote_set
          ON history_vote_set.show_id = sh.id
         AND history_vote_set.voter_id = %s
         {result_mode_filter}
        WHERE account.id <> %s {revote_filter}
        ORDER BY username_sort, account.username, account.id
    """,
        (user_id, user_id),
    )
    user_list = [dict(r) for r in cursor.fetchall()]

    selected_id = request.args.get("user", type=int)
    selected_user = None
    regular_entries: list[dict] = []
    special_entries: list[dict] = []
    if selected_id:
        selected_user = next((u for u in user_list if u["id"] == selected_id), None)
        if selected_user:
            entries = _submitter_entries(cursor, user_id, selected_user["id"], revote=revote)
            # Specials use negative year ids; split them into their own table.
            regular_entries = [e for e in entries if (e["year_id"] or 0) >= 0]
            special_entries = [e for e in entries if (e["year_id"] or 0) < 0]
            special_entries.sort(key=lambda e: (e["special_name"] or "", e["country"] or ""))

    return render_template(
        "user/votes.html",
        username=username,
        view="user",
        user_list=user_list,
        selected_user=selected_user,
        regular_entries=regular_entries,
        special_entries=special_entries,
        is_revote=revote,
        history_endpoint="user.revotes" if revote else "user.votes",
    )


def _votes_by_year(cursor, user_id: int, username: str, *, revote=False):
    """Per-year view: a dropdown of every year (and special edition); picking
    one lists that year's entries with the user's points. The year is fixed, so
    the table leads with the country instead of a year column.
    """
    cursor.execute(
        """
        SELECT DISTINCT year.id, year.special_name, year.special_short_name
        FROM year
        JOIN current_song AS song ON song.year_id = year.id
        WHERE year.status = 'closed'
          AND song.main_participant
        ORDER BY year.id DESC
    """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    regular_years = [r for r in rows if r["id"] >= 0]
    special_years = sorted((r for r in rows if r["id"] < 0), key=lambda r: r["special_name"] or "")
    selected_id = request.args.get("year", type=int)
    selected_year = next((r for r in rows if r["id"] == selected_id), None)
    entries = []
    if selected_year:
        entries = _year_entries(cursor, user_id, selected_year["id"], revote=revote)
    return render_template("user/votes.html", username=username, view="year",
                           regular_years=regular_years, special_years=special_years,
                           selected_year=selected_year,
                           year_is_special=selected_year is not None and selected_year["id"] < 0,
                           entries=entries, is_revote=revote,
                           history_endpoint="user.revotes" if revote else "user.votes")


def _medal_table(cursor, user_id: int, username: str, *, revote=False):
    finals_only = request.args.get("finals") == "true"
    ballot_join = """
        JOIN LATERAL (
            SELECT id FROM vote_set
            WHERE show_id = sh.id AND voter_id = %s
            ORDER BY (result_mode = 'revote') DESC
            LIMIT 1
        ) vs ON TRUE
    """ if revote else """
        JOIN vote_set vs ON vs.show_id = sh.id AND vs.voter_id = %s
                         AND vs.result_mode = 'official'
    """
    revote_filter = "AND sh.revote_eligible_at IS NOT NULL" if revote else ""
    final_filter = "AND sh.short_name = 'f'" if finals_only else ""
    cursor.execute(
        f"""
        SELECT country.id AS cc, country.name AS country,
               COUNT(*) FILTER (WHERE point.place = 1) AS first,
               COUNT(*) FILTER (WHERE point.place = 2) AS second,
               COUNT(*) FILTER (WHERE point.place = 3) AS third,
               COUNT(*) FILTER (WHERE point.place = 4) AS fourth,
               COUNT(*) FILTER (WHERE point.place = 5) AS fifth,
               COUNT(DISTINCT sh.id) AS votings
        FROM show sh
        {ballot_join}
        JOIN song_show ss ON ss.show_id = sh.id
        JOIN current_song AS song ON song.id = ss.song_id
        JOIN country ON country.id = song.country_id
        LEFT JOIN vote ON vote.vote_set_id = vs.id AND vote.song_id = song.id
        LEFT JOIN point ON point.point_system_id = sh.point_system_id
                       AND point.score = vote.score
        WHERE sh.status = 'full' {revote_filter} {final_filter}
          AND sh.national_final_id IS NULL
        GROUP BY country.id, country.name
        ORDER BY first DESC, second DESC, third DESC, fourth DESC, fifth DESC,
                 votings ASC, country.name ASC
        """,
        (user_id,),
    )
    return render_template(
        "user/votes.html",
        username=username,
        view="medals",
        medals=cursor.fetchall(),
        finals_only=finals_only,
        is_revote=revote,
        history_endpoint="user.revotes" if revote else "user.votes",
    )


def _load_vote_history_points(cursor, votes: list[dict], *, unredacted: bool) -> None:
    """Attach every ballot's entries using one metadata-complete query."""
    vote_set_ids = [vote["id"] for vote in votes]
    points_by_vote_set: dict[int, list[dict]] = {
        vote_set_id: [] for vote_set_id in vote_set_ids
    }
    if not vote_set_ids:
        return

    cursor.execute(
        """
        SELECT vote.vote_set_id, vote.score AS pts,
               data.title, data.artist,
               song.country_id AS code, country.name, song.id,
               source_show.short_name AS source_short_name,
               source_show.status AS source_status,
               final_membership.song_id IS NOT NULL AS in_final,
               sc_membership.song_id IS NOT NULL AS in_sc,
               result.place AS result_place
        FROM vote
        JOIN vote_set ON vote_set.id = vote.vote_set_id
        JOIN show AS source_show ON source_show.id = vote_set.show_id
        JOIN song ON song.id = vote.song_id
        JOIN LATERAL (
            SELECT song_data.title,
                   artist_credit_name(song_data.artist_credit_set_id) AS artist,
                   song_data.artist_credit_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN country ON country.id = song.country_id
        LEFT JOIN show AS final_show
          ON final_show.year_id = source_show.year_id
         AND final_show.short_name = 'f'
         AND final_show.national_final_id IS NULL
        LEFT JOIN song_show AS final_membership
          ON final_membership.show_id = final_show.id
         AND final_membership.song_id = song.id
        LEFT JOIN show AS sc_show
          ON sc_show.year_id = source_show.year_id
         AND sc_show.short_name = 'sc'
         AND sc_show.national_final_id IS NULL
        LEFT JOIN song_show AS sc_membership
          ON sc_membership.show_id = sc_show.id
         AND sc_membership.song_id = song.id
        LEFT JOIN country_show_results AS result
          ON result.show_id = vote_set.show_id
         AND result.song_id = song.id
         AND result.result_mode = 'official'
        WHERE vote.vote_set_id = ANY(%s)
          AND data.title IS NOT NULL
          AND data.artist_credit_set_id IS NOT NULL
        ORDER BY vote.vote_set_id, vote.score DESC
        """,
        (vote_set_ids,),
    )

    for row in cursor.fetchall():
        in_hidden_show = False
        if row["source_short_name"] != "f":
            if row["in_final"]:
                row["class"] = "qualifier f-qualifier"
                in_hidden_show = row["source_status"] == "partial"
            if row["source_short_name"] != "sc" and row["in_sc"]:
                row["class"] = "qualifier sc-qualifier"
                in_hidden_show |= row["source_status"] == "partial"

        if in_hidden_show and not unredacted:
            row["title"] = ""
            row["artist"] = ""
            row["name"] = ""
            row["code"] = "XX"
        if in_hidden_show or row["code"] == "XX":
            row["result_place"] = None

        row.pop("source_short_name")
        row.pop("source_status")
        row.pop("in_final")
        row.pop("in_sc")
        points_by_vote_set[row.pop("vote_set_id")].append(row)

    for vote in votes:
        vote["points"] = points_by_vote_set[vote["id"]]


def _load_revote_history_points(cursor, votes: list[dict]) -> None:
    """Attach revote entries, original scores, and result metadata in bulk."""
    vote_set_ids = [vote["id"] for vote in votes]
    points_by_vote_set: dict[int, list[dict]] = {
        vote_set_id: [] for vote_set_id in vote_set_ids
    }
    has_original_vote: dict[int, bool] = {
        vote_set_id: False for vote_set_id in vote_set_ids
    }
    if not vote_set_ids:
        return

    cursor.execute(
        """
        SELECT revote_set.id AS vote_set_id,
               ballot_vote.pts,
               data.title, song.country_id AS code, song.id,
               official_set.id IS NOT NULL AS has_original_vote,
               ballot_vote.original_score,
               result.place AS result_place,
               result.special_qualifier,
               progression.priority AS progression_priority
        FROM vote_set AS revote_set
        LEFT JOIN vote_set AS official_set
          ON official_set.voter_id = revote_set.voter_id
         AND official_set.show_id = revote_set.show_id
         AND official_set.result_mode = 'official'
        JOIN LATERAL (
            SELECT ballot_song.song_id,
                   COALESCE(MAX(ballot_song.revote_score), 0) AS pts,
                   MAX(ballot_song.original_score) AS original_score
            FROM (
                SELECT vote.song_id, vote.score AS revote_score,
                       NULL::integer AS original_score
                FROM vote
                WHERE vote.vote_set_id = revote_set.id
                UNION ALL
                SELECT vote.song_id, NULL::integer AS revote_score,
                       vote.score AS original_score
                FROM vote
                WHERE vote.vote_set_id = official_set.id
            ) AS ballot_song
            GROUP BY ballot_song.song_id
        ) AS ballot_vote ON true
        JOIN song ON song.id = ballot_vote.song_id
        JOIN LATERAL (
            SELECT song_data.title,
                   artist_credit_name(song_data.artist_credit_set_id) AS artist,
                   song_data.artist_credit_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        LEFT JOIN country_show_results AS result
          ON result.show_id = revote_set.show_id
         AND result.song_id = song.id
         AND result.result_mode = 'revote'
        LEFT JOIN LATERAL (
            SELECT progression.priority
            FROM show_progression AS progression
            JOIN show AS target ON target.id = progression.target_show_id
            WHERE progression.source_show_id = revote_set.show_id
              AND target.short_name = result.entry_status
            LIMIT 1
        ) AS progression ON true
        WHERE revote_set.id = ANY(%s)
          AND data.title IS NOT NULL
          AND data.artist_credit_set_id IS NOT NULL
        ORDER BY revote_set.id, ballot_vote.pts DESC,
                 CASE WHEN ballot_vote.pts = 0
                      THEN ballot_vote.original_score END DESC,
                 song.id
        """,
        (vote_set_ids,),
    )

    for row in cursor.fetchall():
        vote_set_id = row.pop("vote_set_id")
        has_original_vote[vote_set_id] = row.pop("has_original_vote")
        original_score = row.pop("original_score") or 0
        progression_priority = row.pop("progression_priority")
        row["special_qualifier"] = bool(row["special_qualifier"])
        row["points_difference"] = row["pts"] - original_score
        row["class"] = (
            "qualifier"
            if progression_priority == 1
            else "sc-qualifier" if progression_priority is not None else ""
        )
        points_by_vote_set[vote_set_id].append(row)

    for vote in votes:
        vote["has_original_vote"] = has_original_vote[vote["id"]]
        vote["points"] = points_by_vote_set[vote["id"]]


@bp.get("/<username>/votes")
@with_auth
def votes(username: str, user: tuple[int, str] | None, permissions: UserPermissions):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id FROM account WHERE LOWER(username) = LOWER(%s)
    """,
        (username,),
    )
    user_id = cursor.fetchone()
    if not user_id:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id["id"]

    # A user may opt to see votes for not-yet-revealed shows unblanked
    # (?hidden=false) when looking at their own history, or when they hold a
    # role that can_view_restricted (admins/staff). Everyone else stays redacted.
    is_owner = user is not None and user[0] == user_id
    can_reveal = is_owner or permissions.can_view_restricted
    unredacted = can_reveal and request.args.get("hidden") == "false"

    if request.args.get("view") == "country":
        return _votes_by_country(cursor, user_id, username)
    if request.args.get("view") == "user":
        return _votes_by_user(cursor, user_id, username)
    if request.args.get("view") == "year":
        return _votes_by_year(cursor, user_id, username)
    if request.args.get("view") == "medals":
        return _medal_table(cursor, user_id, username)

    selected_editions, selected_rounds, filter_sql, filter_parameters = (
        _vote_history_filters()
    )
    selected_statuses = _vote_history_statuses()
    year_from, year_to, year_filter_sql, year_filter_parameters = (
        _vote_history_year_filter()
    )
    history_years = _vote_history_years(cursor)
    cursor.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM vote_set
        JOIN show ON vote_set.show_id = show.id
        WHERE vote_set.voter_id = %s AND vote_set.result_mode = 'official'
          AND show.status = ANY(%s)
          AND {filter_sql}
          AND {year_filter_sql}
        """,
        (
            user_id,
            list(selected_statuses),
            *filter_parameters,
            *year_filter_parameters,
        ),
    )
    pagination = _vote_history_pagination(
        cursor.fetchone()["total"], "user.votes", username
    )
    cursor.execute(
        f"""
        SELECT vote_set.id, vote_set.show_id, account.username, nickname, country_id,
               show.show_name, show.short_name, show.date, show.year_id, show.status,
               year.special_name, year.special_short_name,
               national_final.name AS national_final_name,
               national_final.short_name AS national_final_short_name
        FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN show ON vote_set.show_id = show.id
        LEFT JOIN year ON show.year_id = year.id
        LEFT JOIN national_final ON national_final.id = show.national_final_id
        WHERE vote_set.voter_id = %s AND vote_set.result_mode = 'official'
          AND show.status = ANY(%s)
          AND {filter_sql}
          AND {year_filter_sql}
        ORDER BY show.date DESC NULLS LAST, show.id DESC
        LIMIT %s OFFSET %s
    """,
        (
            user_id,
            list(selected_statuses),
            *filter_parameters,
            *year_filter_parameters,
            VOTE_HISTORY_PAGE_SIZE,
            pagination["offset"],
        ),
    )
    votes = []
    for row in cursor.fetchall():
        val = {
            "id": row["id"],
            "show_id": row["show_id"],
            "username": row["username"],
            "nickname": row["nickname"] or username,
            "code": row["country_id"],
            "show_name": row["show_name"],
            "short_name": (
                f"{row['national_final_short_name']}-{row['short_name']}"
                if row["national_final_short_name"]
                else row["short_name"]
            ),
            "status": row["status"],
            "date": row["date"],
            "year": row["year_id"],
            "special_name": row["special_name"],
            "special_short_name": row["special_short_name"],
            "national_final_name": row["national_final_name"],
        }
        votes.append(val)

    _load_vote_history_points(cursor, votes, unredacted=unredacted)

    return render_template(
        "user/votes.html", votes=votes, username=username, view="shows",
        can_reveal=can_reveal, unredacted=unredacted,
        selected_editions=selected_editions, selected_rounds=selected_rounds,
        selected_statuses=selected_statuses,
        history_years=history_years, year_from=year_from, year_to=year_to,
        **pagination,
    )


@bp.get("/<username>/revotes")
@with_auth
def revotes(username: str, user: tuple[int, str] | None, permissions: UserPermissions):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    cursor = get_db().cursor()
    cursor.execute("SELECT id FROM account WHERE LOWER(username) = LOWER(%s)", (username,))
    account = cursor.fetchone()
    if not account:
        return render_template("error.html", error="User not found"), 404
    user_id = account["id"]

    if request.args.get("view") == "country":
        return _votes_by_country(cursor, user_id, username, revote=True)
    if request.args.get("view") == "user":
        return _votes_by_user(cursor, user_id, username, revote=True)
    if request.args.get("view") == "year":
        return _votes_by_year(cursor, user_id, username, revote=True)
    if request.args.get("view") == "medals":
        return _medal_table(cursor, user_id, username, revote=True)

    selected_editions, selected_rounds, filter_sql, filter_parameters = (
        _vote_history_filters()
    )
    year_from, year_to, year_filter_sql, year_filter_parameters = (
        _vote_history_year_filter()
    )
    history_years = _vote_history_years(cursor)
    cursor.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM vote_set
        JOIN show ON show.id = vote_set.show_id
        WHERE vote_set.voter_id = %s AND vote_set.result_mode = 'revote'
          AND show.status = 'full'
          AND {filter_sql}
          AND {year_filter_sql}
        """,
        (user_id, *filter_parameters, *year_filter_parameters),
    )
    pagination = _vote_history_pagination(
        cursor.fetchone()["total"], "user.revotes", username
    )
    cursor.execute(
        f"""
        SELECT vote_set.id, vote_set.show_id, vote_set.nickname, vote_set.country_id,
               show.show_name, show.short_name, show.date, show.year_id,
               year.special_name, year.special_short_name,
               national_final.name AS national_final_name,
               national_final.short_name AS national_final_short_name
        FROM vote_set
        JOIN show ON show.id = vote_set.show_id
        LEFT JOIN year ON year.id = show.year_id
        LEFT JOIN national_final ON national_final.id = show.national_final_id
        WHERE vote_set.voter_id = %s AND vote_set.result_mode = 'revote'
          AND show.status = 'full'
          AND {filter_sql}
          AND {year_filter_sql}
        ORDER BY show.date DESC NULLS LAST, show.id DESC
        LIMIT %s OFFSET %s
        """,
        (
            user_id,
            *filter_parameters,
            *year_filter_parameters,
            VOTE_HISTORY_PAGE_SIZE,
            pagination["offset"],
        ),
    )
    votes = [
        {
            "id": row["id"],
            "show_id": row["show_id"],
            "nickname": row["nickname"] or username,
            "code": row["country_id"],
            "show_name": row["show_name"],
            "short_name": (
                f"{row['national_final_short_name']}-{row['short_name']}"
                if row["national_final_short_name"]
                else row["short_name"]
            ),
            "date": row["date"],
            "year": row["year_id"],
            "special_name": row["special_name"],
            "special_short_name": row["special_short_name"],
            "national_final_name": row["national_final_name"],
        }
        for row in cursor.fetchall()
    ]
    _load_revote_history_points(cursor, votes)

    return render_template(
        "user/votes.html",
        votes=votes,
        username=username,
        view="shows",
        is_revote=True,
        history_endpoint="user.revotes",
        selected_editions=selected_editions,
        selected_rounds=selected_rounds,
        history_years=history_years,
        year_from=year_from,
        year_to=year_to,
        **pagination,
    )


@bp.get("/<username>/predictions")
def predictions(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id FROM account WHERE LOWER(username) = LOWER(%s)
    """,
        (username,),
    )
    user_id = cursor.fetchone()
    if not user_id:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id["id"]

    cursor.execute(
        """
        SELECT prediction_set.id, prediction_set.show_id, prediction_set.created_at,
               show.show_name, show.short_name, show.date, show.year_id, show.status,
               year.special_name, year.special_short_name,
               national_final.name AS national_final_name,
               national_final.short_name AS national_final_short_name
        FROM prediction_set
        JOIN show ON prediction_set.show_id = show.id
        LEFT JOIN year ON show.year_id = year.id
        LEFT JOIN national_final ON national_final.id = show.national_final_id
        WHERE prediction_set.user_id = %s
          AND show.status = 'full'
        ORDER BY show.date DESC
    """,
        (user_id,),
    )
    predictions = []
    for row in cursor.fetchall():
        predictions.append({
            "id": row["id"],
            "show_id": row["show_id"],
            "show_name": row["show_name"],
            "short_name": (
                f"{row['national_final_short_name']}-{row['short_name']}"
                if row["national_final_short_name"]
                else row["short_name"]
            ),
            "status": row["status"],
            "date": row["date"].strftime("%d %b %Y"),
            "year": row["year_id"],
            "special_name": row["special_name"],
            "special_short_name": row["special_short_name"],
            "national_final_name": row["national_final_name"],
        })

    show_ids = list({p["show_id"] for p in predictions})
    set_rank: dict[int, tuple[int, int]] = {}  # set_id -> (rank, total predictors)
    if show_ids:
        # Per-set total score, computed in SQL across every predictor for these shows.
        # Ties broken by last-submission time — updated_at if present, else created_at.
        cursor.execute(
            """
            SELECT prediction_set.id AS set_id,
                   prediction_set.show_id,
                   COALESCE(prediction_set.updated_at, prediction_set.created_at)
                       AS submitted_at,
                   COALESCE(SUM(POWER(csr.place - prediction.position, 2)), 0)::int
                       AS score
            FROM prediction_set
            JOIN prediction ON prediction.set_id = prediction_set.id
            LEFT JOIN country_show_results csr
             ON csr.show_id = prediction_set.show_id
             AND csr.song_id = prediction.song_id
             AND csr.result_mode = 'official'
            WHERE prediction_set.show_id = ANY(%s)
            GROUP BY prediction_set.id, prediction_set.show_id
        """,
            (show_ids,),
        )
        scores_by_show: defaultdict[int, list[tuple]] = defaultdict(list)
        for row in cursor.fetchall():
            scores_by_show[row["show_id"]].append(
                (row["set_id"], row["score"], row["submitted_at"])
            )
        for _sid, rows in scores_by_show.items():
            rows.sort(key=lambda r: (r[1], r[2]))
            total = len(rows)
            for i, (set_id, _score, _ts) in enumerate(rows, start=1):
                set_rank[set_id] = (i, total)

    points_by_set: dict[int, list[dict]] = {ps["id"]: [] for ps in predictions}
    prediction_set_ids = list(points_by_set)
    if prediction_set_ids:
        cursor.execute(
            """
            SELECT prediction.set_id, prediction.position AS pos,
                   data.title, data.artist,
                   song.country_id AS code, country.name, song.id,
                   result.place AS result_place
            FROM prediction
            JOIN prediction_set ON prediction_set.id = prediction.set_id
            JOIN song ON song.id = prediction.song_id
            JOIN LATERAL (
                SELECT song_data.title,
                       artist_credit_name(song_data.artist_credit_set_id) AS artist,
                       song_data.artist_credit_set_id
                FROM song_data
                WHERE song_data.song_id = song.id
                   OR (
                       song_data.song_id IS NULL
                       AND song_data.country_id = song.country_id
                       AND song_data.year_id = song.year_id
                       AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
                   )
                ORDER BY song_data.created_at DESC, song_data.id DESC
                LIMIT 1
            ) AS data ON true
            JOIN country ON country.id = song.country_id
            LEFT JOIN country_show_results AS result
              ON result.show_id = prediction_set.show_id
             AND result.song_id = song.id
             AND result.result_mode = 'official'
            WHERE prediction.set_id = ANY(%s)
              AND data.title IS NOT NULL
              AND data.artist_credit_set_id IS NOT NULL
            ORDER BY prediction.set_id, prediction.position
            """,
            (prediction_set_ids,),
        )
        for row in cursor.fetchall():
            points_by_set[row.pop("set_id")].append(row)

    for ps in predictions:
        items = points_by_set[ps["id"]]
        score = 0
        for val in items:
            real = val["result_place"]
            if real is not None:
                val["penalty"] = (real - val["pos"]) ** 2
                score += val["penalty"]
            else:
                val["penalty"] = None
        ps["points"] = items
        ps["score"] = score
        rank_info = set_rank.get(ps["id"])
        if rank_info:
            ps["rank"], ps["total_predictors"] = rank_info
        else:
            ps["rank"] = None
            ps["total_predictors"] = None

    return render_template(
        "user/predictions.html", predictions=predictions, username=username
    )


@bp.get("/<username>/submissions")
def submissions(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id FROM account WHERE LOWER(username) = LOWER(%s)
    """,
        (username,),
    )
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id_g["id"]

    songs = get_user_submission_history(user_id)
    results = get_show_results_for_songs([s.id for s in songs])

    regular_songs = [s for s in songs if s.year.id >= 0 and s.main_participant]
    special_songs = [s for s in songs if s.year.id < 0 and s.main_participant]
    national_final_songs = [s for s in songs if s.national_final_id]
    ten_year_window = set(get_closed_years()[-10:])

    return render_template(
        "user/submissions.html",
        songs=regular_songs,
        special_songs=special_songs,
        national_final_songs=national_final_songs,
        username=username,
        results=results,
        stats=_user_submission_stats(
            regular_songs,
            results,
            ten_year_window=ten_year_window,
            double_entry=get_double_entry_stats(user_id),
        ),
        special_stats=(
            _user_submission_stats(special_songs, results, special=True)
            if special_songs
            else None
        ),
        format_decimal=_format_decimal,
    )


@overload
def _parse_bias_filters(with_specials: Literal[True]) -> tuple[int | None, int | None, bool]: ...
@overload
def _parse_bias_filters(with_specials: Literal[False]) -> tuple[int | None, int | None]: ...
def _parse_bias_filters(with_specials: bool):
    """Read ?from, ?to, and (for submitter variants) ?include_specials.

    The form carries a hidden `_submitted` sentinel so we can tell an
    unchecked checkbox (absent from args) apart from a fresh visit with
    no filters set. Fresh visit → default True; form submitted without
    the box → False.
    """
    year_from = request.args.get("from", type=int)
    year_to = request.args.get("to", type=int)
    if request.args.get("_submitted"):
        include_specials = "include_specials" in request.args
    else:
        include_specials = True
    if with_specials:
        return year_from, year_to, include_specials
    return year_from, year_to


def get_country_biases(
    user_id: int, year_from: int | None, year_to: int | None, include_revotes: bool
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM user_country_bias(%s, %s, %s, %s)",
        (user_id, year_from, year_to, include_revotes),
    )
    for r in cursor:
        yield dict(r)


def get_language_biases(
    user_id: int, year_from: int | None, year_to: int | None, include_revotes: bool
):
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT * FROM user_language_bias(%s, %s, %s, %s)",
        (user_id, year_from, year_to, include_revotes),
    )
    for row in cursor:
        yield dict(row)


def get_submitter_biases(
    user_id: int, year_from: int | None, year_to: int | None,
    include_specials: bool, include_revotes: bool,
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM user_submitter_bias(%s, %s, %s, %s, %s)",
        (user_id, year_from, year_to, include_specials, include_revotes),
    )
    for r in cursor:
        yield dict(r)


@bp.get("/<username>/bias")
def bias(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    bias_type = request.args.get("type", "country")

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id FROM account WHERE LOWER(username) = LOWER(%s)
    """,
        (username,),
    )
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return {"error": "User not found"}, 404
    user_id = user_id_g["id"]
    include_revotes = request.args.get("include_revotes") == "true"

    if bias_type == "user":
        year_from, year_to, include_specials = _parse_bias_filters(with_specials=True)
        biases = get_submitter_biases(
            user_id, year_from, year_to, include_specials, include_revotes
        )
    elif bias_type == "country":
        year_from, year_to = _parse_bias_filters(with_specials=False)
        include_specials = True  # N/A; template reads it for checkbox state only
        biases = get_country_biases(user_id, year_from, year_to, include_revotes)
    elif bias_type == "language":
        year_from, year_to = _parse_bias_filters(with_specials=False)
        include_specials = True
        biases = get_language_biases(user_id, year_from, year_to, include_revotes)
    else:
        return render_template(
            "error.html", error=f"Invalid bias type specified: {bias_type}."
        ), 400

    return render_template(
        "user/bias.html",
        username=username,
        bias_type=bias_type,
        biases=biases,
        closed_years=get_closed_years(),
        year_from=year_from,
        year_to=year_to,
        include_specials=include_specials,
        include_revotes=include_revotes,
    )


def get_taste_similarity(
    user_id: int,
    year_from: int | None,
    year_to: int | None,
    include_specials: bool,
    include_revotes: bool = True,
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM user_taste_similarity(%s, %s, %s, %s, %s)",
        (user_id, year_from, year_to, include_specials, include_revotes),
    )
    for r in cursor:
        yield dict(r)


@bp.get("/<username>/similar")
def similar(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id FROM account WHERE LOWER(username) = LOWER(%s)", (username,))
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id_g["id"]

    year_from, year_to, include_specials = _parse_bias_filters(with_specials=True)
    if "include_revotes" in request.args:
        include_revotes = request.args.get("include_revotes") == "true"
    else:
        include_revotes = not request.args.get("_submitted")
    similarities = get_taste_similarity(
        user_id, year_from, year_to, include_specials, include_revotes
    )

    return render_template(
        "user/similar.html",
        username=username,
        similarities=similarities,
        closed_years=get_closed_years(),
        year_from=year_from,
        year_to=year_to,
        include_specials=include_specials,
        include_revotes=include_revotes,
    )


@bp.get("/<username>/bias/for")
def bias_for(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM account WHERE LOWER(username) = LOWER(%s)", (username,))
    row = cursor.fetchone()
    if not row:
        return render_template("error.html", error="User not found"), 404

    year_from, year_to, include_specials = _parse_bias_filters(with_specials=True)
    include_revotes = request.args.get("include_revotes") == "true"
    cursor.execute(
        "SELECT * FROM submitter_voter_bias(%s, %s, %s, %s, %s)",
        (row["id"], year_from, year_to, include_specials, include_revotes),
    )
    biases = [dict(r) for r in cursor]

    return render_template(
        "inbound_bias.html",
        subject_type="user",
        subject_name=username,
        biases=biases,
        closed_years=get_closed_years(),
        year_from=year_from,
        year_to=year_to,
        include_specials=include_specials,
        include_revotes=include_revotes,
    )
