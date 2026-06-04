import unicodedata
import urllib.parse
from collections import defaultdict
from typing import Literal, overload

from flask import Blueprint, request

from ..db import fetchone, get_db
from ..utils import (
    get_closed_years,
    get_show_results_for_songs,
    get_user_role_from_session,
    get_user_songs,
    render_template,
)

bp = Blueprint("user", __name__, url_prefix="/user")


@bp.get("/")
def index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id, username, role FROM account
        ORDER BY username
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

    return render_template("user/page.html", username=username)


def redact_song_if_show(
    song: dict, year: int, show_short_name: str, status: str
) -> tuple[bool, bool]:
    db = get_db()
    cursor = db.cursor()
    show_exists = False
    song_modified = False

    cursor.execute(
        """
        SELECT id FROM show WHERE year_id = %s AND short_name = %s
    """,
        (year, show_short_name),
    )
    show = cursor.fetchone()
    if show:
        show_exists = True
        cursor.execute(
            """
            SELECT COUNT(*) AS c FROM song_show
            WHERE show_id = %s AND song_id = %s
        """,
            (show["id"], song["id"]),
        )
        if fetchone(cursor)["c"] > 0:
            song_modified = True
            song["class"] = f"qualifier {show_short_name}-qualifier"
            if status == "partial":
                song["title"] = ""
                song["artist"] = ""
                song["country"] = ""
                song["code"] = "XX"

    return (show_exists, song_modified)


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


def _aggregate_entries(rows) -> list[dict]:
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
                "total": 0,
                "max_possible": 0,
                "final": {"part": False, "pts": None},
                "repe": {"part": False, "pts": None},
                "semi": {"part": False, "pts": None},
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

    entries = [g for g in groups.values() if g["max_possible"] > 0]
    for g in entries:
        g["pct"] = (g["total"] / g["max_possible"] * 100) if g["max_possible"] else 0.0
    return entries


def _fetch_entries(cursor, voter_id: int, where_sql: str, where_val) -> list[dict]:
    """``voter_id``'s vote totals over the entries selected by ``where_sql``,
    oldest year first.

    Driven off ``song_show`` (every show the entry actually competed in) and
    left-joined to the voter's votes, so the per-show columns can distinguish a
    show the entry sat out from one it competed in but earned no points. Only
    fully-revealed ('full') shows are counted, so partial-result shows can't
    leak which entries qualified. ``where_sql`` is a trusted constant (never
    user input); the matching value is always parameterised.
    """
    cursor.execute(
        f"""
        SELECT sh.short_name, sh.year_id,
               year.special_name, year.special_short_name,
               song.id AS song_id, song.title, song.artist, song.entry_number,
               country.id AS cc, country.name AS country,
               vote_set.id AS vote_set_id, vote.score AS score,
               (SELECT MAX(point.score) FROM point
                WHERE point.point_system_id = sh.point_system_id) AS show_max
        FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN show sh ON song_show.show_id = sh.id AND sh.status = 'full'
        JOIN country ON song.country_id = country.id
        LEFT JOIN year ON sh.year_id = year.id
        LEFT JOIN vote_set ON vote_set.show_id = sh.id AND vote_set.voter_id = %s
        LEFT JOIN vote ON vote.vote_set_id = vote_set.id AND vote.song_id = song.id
        WHERE {where_sql}
    """,
        (voter_id, where_val),
    )
    entries = _aggregate_entries(cursor.fetchall())
    entries.sort(key=lambda g: (g["year_id"] or 0, g["country"] or ""))
    return entries


def _country_entries(cursor, voter_id: int, code: str) -> list[dict]:
    """``voter_id``'s vote totals for one country's entries."""
    return _fetch_entries(cursor, voter_id, "song.country_id = %s", code)


def _submitter_entries(cursor, voter_id: int, submitter_id: int) -> list[dict]:
    """``voter_id``'s vote totals for entries submitted by ``submitter_id``."""
    return _fetch_entries(cursor, voter_id, "song.submitter_id = %s", submitter_id)


def _year_entries(cursor, voter_id: int, year_id: int) -> list[dict]:
    """``voter_id``'s vote totals for every entry in one year (by country)."""
    return _fetch_entries(cursor, voter_id, "song.year_id = %s", year_id)


def _votes_by_country(cursor, user_id: int, username: str):
    """Per-country view: a dropdown of every participating country; picking one
    lists that country's entries with the user's points. Regular-year entries
    (oldest first) and special editions (by name) are split into two tables.
    """
    cursor.execute(
        """
        SELECT DISTINCT country.id AS cc, country.name
        FROM country
        JOIN song ON song.country_id = country.id
        ORDER BY country.name
    """
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
            entries = _country_entries(cursor, user_id, selected_country["cc"])
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
    )


def _votes_by_user(cursor, user_id: int, username: str):
    """Per-submitter view: a dropdown of every submitter; picking one lists the
    entries they submitted with this user's points. Like the per-country view
    but spanning countries, so the tables also carry a country column.
    """
    cursor.execute(
        """
        SELECT DISTINCT account.id, account.username
        FROM account
        JOIN song ON song.submitter_id = account.id
        ORDER BY account.username
    """
    )
    user_list = [dict(r) for r in cursor.fetchall()]

    selected_id = request.args.get("user", type=int)
    selected_user = None
    regular_entries: list[dict] = []
    special_entries: list[dict] = []
    if selected_id:
        selected_user = next((u for u in user_list if u["id"] == selected_id), None)
        if selected_user:
            entries = _submitter_entries(cursor, user_id, selected_user["id"])
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
    )


def _votes_by_year(cursor, user_id: int, username: str):
    """Per-year view: a dropdown of every year (and special edition); picking
    one lists that year's entries with the user's points. The year is fixed, so
    the table leads with the country instead of a year column.
    """
    cursor.execute(
        """
        SELECT DISTINCT year.id, year.special_name, year.special_short_name
        FROM year
        JOIN song ON song.year_id = year.id
        WHERE year.status = 'closed'
        ORDER BY year.id DESC
    """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    regular_years = [r for r in rows if r["id"] >= 0]
    special_years = sorted(
        (r for r in rows if r["id"] < 0), key=lambda r: r["special_name"] or ""
    )

    selected_id = request.args.get("year", type=int)
    selected_year = None
    entries: list[dict] = []
    if selected_id is not None:
        selected_year = next((r for r in rows if r["id"] == selected_id), None)
        if selected_year:
            entries = _year_entries(cursor, user_id, selected_year["id"])

    return render_template(
        "user/votes.html",
        username=username,
        view="year",
        regular_years=regular_years,
        special_years=special_years,
        selected_year=selected_year,
        year_is_special=selected_year is not None and selected_year["id"] < 0,
        entries=entries,
    )


@bp.get("/<username>/votes")
def votes(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id FROM account WHERE username = %s
    """,
        (username,),
    )
    user_id = cursor.fetchone()
    if not user_id:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id["id"]

    if request.args.get("view") == "country":
        return _votes_by_country(cursor, user_id, username)
    if request.args.get("view") == "user":
        return _votes_by_user(cursor, user_id, username)
    if request.args.get("view") == "year":
        return _votes_by_year(cursor, user_id, username)

    cursor.execute(
        """
        SELECT vote_set.id, vote_set.show_id, account.username, nickname, country_id,
               show.show_name, show.short_name, show.date, show.year_id, show.status,
               year.special_name, year.special_short_name
        FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN show ON vote_set.show_id = show.id
        LEFT JOIN year ON show.year_id = year.id
        WHERE vote_set.voter_id = %s AND (show.status = 'full' OR show.status = 'partial')
        ORDER BY show.date DESC
    """,
        (user_id,),
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
            "short_name": row["short_name"],
            "status": row["status"],
            "date": row["date"].strftime("%d %b %Y"),
            "year": row["year_id"],
            "special_name": row["special_name"],
            "special_short_name": row["special_short_name"],
        }
        votes.append(val)

    # Batch-fetch show results for all shows this user voted in,
    # keyed by (show_id, song_id) → place.
    show_ids = list({v["show_id"] for v in votes})
    show_results: dict[tuple[int, int], int] = {}
    if show_ids:
        cursor.execute(
            """
            SELECT show_id, song_id, place
            FROM country_show_results
            WHERE show_id = ANY(%s)
        """,
            (show_ids,),
        )
        for row in cursor.fetchall():
            show_results[(row["show_id"], row["song_id"])] = row["place"]

    for vote in votes:
        cursor.execute(
            """
            SELECT score AS pts, song.title, song.artist,
                   song.country_id AS code, country.name, song.id
            FROM vote
            JOIN song ON vote.song_id = song.id
            JOIN country ON song.country_id = country.id
            WHERE vote.vote_set_id = %s
            ORDER BY score DESC
        """,
            (vote["id"],),
        )
        songs = []
        for val in cursor.fetchall():
            if vote["short_name"] != "f":
                redact_song_if_show(val, vote["year"], "f", vote["status"])
                if vote["short_name"] != "sc":
                    redact_song_if_show(val, vote["year"], "sc", vote["status"])
            # Only show result placement for non-redacted songs.
            if val.get("code") == "XX":
                val["result_place"] = None
            else:
                val["result_place"] = show_results.get((vote["show_id"], val["id"]))
            songs.append(val)

        vote["points"] = songs

    return render_template(
        "user/votes.html", votes=votes, username=username, view="shows"
    )


@bp.get("/<username>/predictions")
def predictions(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id FROM account WHERE username = %s
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
               year.special_name, year.special_short_name
        FROM prediction_set
        JOIN show ON prediction_set.show_id = show.id
        LEFT JOIN year ON show.year_id = year.id
        WHERE prediction_set.user_id = %s AND show.status = 'full'
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
            "short_name": row["short_name"],
            "status": row["status"],
            "date": row["date"].strftime("%d %b %Y"),
            "year": row["year_id"],
            "special_name": row["special_name"],
            "special_short_name": row["special_short_name"],
        })

    show_ids = list({p["show_id"] for p in predictions})
    show_results: dict[tuple[int, int], int] = {}
    set_rank: dict[int, tuple[int, int]] = {}  # set_id -> (rank, total predictors)
    if show_ids:
        cursor.execute(
            """
            SELECT show_id, song_id, place
            FROM country_show_results
            WHERE show_id = ANY(%s)
        """,
            (show_ids,),
        )
        for row in cursor.fetchall():
            show_results[(row["show_id"], row["song_id"])] = row["place"]

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
        for sid, rows in scores_by_show.items():
            rows.sort(key=lambda r: (r[1], r[2]))
            total = len(rows)
            for i, (set_id, _score, _ts) in enumerate(rows, start=1):
                set_rank[set_id] = (i, total)

    for ps in predictions:
        cursor.execute(
            """
            SELECT prediction.position AS pos, song.title, song.artist,
                   song.country_id AS code, country.name, song.id
            FROM prediction
            JOIN song ON prediction.song_id = song.id
            JOIN country ON song.country_id = country.id
            WHERE prediction.set_id = %s
            ORDER BY prediction.position
        """,
            (ps["id"],),
        )
        items = []
        score = 0
        for val in cursor.fetchall():
            real = show_results.get((ps["show_id"], val["id"]))
            val["result_place"] = real
            if real is not None:
                val["penalty"] = (real - val["pos"]) ** 2
                score += val["penalty"]
            else:
                val["penalty"] = None
            items.append(val)
        items.sort(key=lambda x: (x["result_place"] is None, x["result_place"]))
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
        SELECT id FROM account WHERE username = %s
    """,
        (username,),
    )
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id_g["id"]

    songs = get_user_songs(user_id, select_languages=True)
    results = get_show_results_for_songs([s.id for s in songs])

    regular_songs = [s for s in songs if s.year.id >= 0]
    special_songs = [s for s in songs if s.year.id < 0]

    return render_template(
        "user/submissions.html",
        songs=regular_songs,
        special_songs=special_songs,
        username=username,
        results=results,
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


def get_country_biases(user_id: int, year_from: int | None, year_to: int | None):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM user_country_bias(%s, %s, %s)", (user_id, year_from, year_to))
    for r in cursor:
        yield dict(r)


def get_submitter_biases(
    user_id: int, year_from: int | None, year_to: int | None, include_specials: bool
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM user_submitter_bias(%s, %s, %s, %s)",
        (user_id, year_from, year_to, include_specials),
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
        SELECT id FROM account WHERE username = %s
    """,
        (username,),
    )
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return {"error": "User not found"}, 404
    user_id = user_id_g["id"]

    if bias_type == "user":
        year_from, year_to, include_specials = _parse_bias_filters(with_specials=True)
        biases = get_submitter_biases(user_id, year_from, year_to, include_specials)
    elif bias_type == "country":
        year_from, year_to = _parse_bias_filters(with_specials=False)
        include_specials = True  # N/A; template reads it for checkbox state only
        biases = get_country_biases(user_id, year_from, year_to)
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
    )


def get_taste_similarity(
    user_id: int, year_from: int | None, year_to: int | None, include_specials: bool
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM user_taste_similarity(%s, %s, %s, %s)",
        (user_id, year_from, year_to, include_specials),
    )
    for r in cursor:
        yield dict(r)


@bp.get("/<username>/similar")
def similar(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id FROM account WHERE username = %s", (username,))
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return render_template("error.html", error="User not found"), 404
    user_id = user_id_g["id"]

    year_from, year_to, include_specials = _parse_bias_filters(with_specials=True)
    similarities = get_taste_similarity(user_id, year_from, year_to, include_specials)

    return render_template(
        "user/similar.html",
        username=username,
        similarities=similarities,
        closed_years=get_closed_years(),
        year_from=year_from,
        year_to=year_to,
        include_specials=include_specials,
    )


@bp.get("/<username>/bias/for")
def bias_for(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize("NFKC", username)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM account WHERE username = %s", (username,))
    row = cursor.fetchone()
    if not row:
        return render_template("error.html", error="User not found"), 404

    year_from, year_to, include_specials = _parse_bias_filters(with_specials=True)
    cursor.execute(
        "SELECT * FROM submitter_voter_bias(%s, %s, %s, %s)",
        (row["id"], year_from, year_to, include_specials),
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
    )
