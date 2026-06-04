import io
import math
import typing
from collections import defaultdict

from flask import Blueprint, redirect, request, url_for

from ..db import fetchone, get_db
from ..utils import (
    LCG,
    AbstractVoteSequencer,
    ChronologicalVoteSequencer,
    RandomVoteSequencer,
    Show,
    ShowData,
    SuspensefulVoteSequencer,
    dt_now,
    get_show_id,
    get_show_results_for_songs,
    get_show_songs,
    get_user_role_from_session,
    get_votes_for_song,
    get_year_placements,
    get_year_songs,
    get_year_winner,
    render_template,
    resolve_country_code,
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


def get_voter_participation(
    year_id: int, allowed_shows: list[str] | None = None
) -> tuple[list[str], list[dict]]:
    """Cross-show participation table for a year.

    Returns ``(show_short_names, rows)`` where each row is
    ``{"username": str, "cells": {short_name: state}}`` and ``state`` is
    one of:

    - ``"voted-entry"`` — voted AND had a song in that show.
    - ``"voted"`` — voted but had no song in that show.
    - ``"missed"`` — had a song in that show but didn't vote.
    - ``"none"`` — neither voted nor had an entry.

    A user is included if they voted in (or had a non-placeholder entry
    in) any of the included shows. Rows are sorted alphabetically by
    username (case-insensitive).

    ``allowed_shows`` optionally restricts the table to a specific subset
    of show short_names — used to hide shows whose results aren't
    published yet from non-admin viewers. ``None`` means include every
    show in the year.
    """
    db = get_db()
    cursor = db.cursor()

    if allowed_shows is None:
        cursor.execute(
            "SELECT short_name FROM show WHERE year_id = %s ORDER BY date, id",
            (year_id,),
        )
        short_names = [row["short_name"] for row in cursor.fetchall()]
    else:
        # Preserve the chronological order returned by the canonical query
        # while honouring the caller's allow-list.
        cursor.execute(
            "SELECT short_name FROM show WHERE year_id = %s ORDER BY date, id",
            (year_id,),
        )
        allowed_set = set(allowed_shows)
        short_names = [
            row["short_name"]
            for row in cursor.fetchall()
            if row["short_name"] in allowed_set
        ]

    if not short_names:
        return [], []

    cursor.execute(
        """
        SELECT account.username, show.short_name
        FROM vote_set
        JOIN show ON vote_set.show_id = show.id
        JOIN account ON vote_set.voter_id = account.id
        WHERE show.year_id = %s AND show.short_name = ANY(%s)
        """,
        (year_id, short_names),
    )
    voted: set[tuple[str, str]] = {
        (row["username"], row["short_name"]) for row in cursor.fetchall()
    }

    cursor.execute(
        """
        SELECT DISTINCT account.username, show.short_name
        FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN show ON song_show.show_id = show.id
        JOIN account ON song.submitter_id = account.id
        WHERE show.year_id = %s AND show.short_name = ANY(%s)
          AND NOT song.is_placeholder
        """,
        (year_id, short_names),
    )
    has_entry: set[tuple[str, str]] = {
        (row["username"], row["short_name"]) for row in cursor.fetchall()
    }

    usernames = sorted(
        {u for u, _ in voted} | {u for u, _ in has_entry},
        key=str.lower,
    )

    rows: list[dict] = []
    for username in usernames:
        cells = {}
        for sn in short_names:
            user_voted = (username, sn) in voted
            user_entry = (username, sn) in has_entry
            if user_voted and user_entry:
                cells[sn] = "voted-entry"
            elif user_voted:
                cells[sn] = "voted"
            elif user_entry:
                cells[sn] = "missed"
            else:
                cells[sn] = "none"
        rows.append({"username": username, "cells": cells})

    return short_names, rows


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
def special(short_name: str):
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

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)
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


def _render_year_voters(year_id: int, year_label: str, special_short: str | None, special_name: str | None):
    """Shared body for the regular and special year-voters pages.

    Visibility:
    - Admins (``can_view_restricted``) always see every show in the year.
    - Everyone else only sees shows whose results have been published
      (``status`` is ``partial`` or ``full``). If no show in the year has
      reached that level yet, the page returns an error rather than a
      blank table.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT status FROM year WHERE id = %s", (year_id,))
    year_row = cursor.fetchone()
    if not year_row:
        return render_template("error.html", error="Year not found"), 404

    is_closed = year_row["status"] == "closed"

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    allowed_shows: list[str] | None
    if permissions.can_view_restricted:
        allowed_shows = None
    else:
        cursor.execute(
            """
            SELECT short_name FROM show
            WHERE year_id = %s AND status IN ('partial', 'full')
            ORDER BY date, id
            """,
            (year_id,),
        )
        allowed_shows = [row["short_name"] for row in cursor.fetchall()]
        if not allowed_shows:
            return render_template(
                "error.html",
                error="No shows in this year have published results yet",
            ), 403

    show_short_names, rows = get_voter_participation(year_id, allowed_shows)
    return render_template(
        "year/voters_overview.html",
        year=year_label,
        special=special_short,
        special_name=special_name,
        is_closed=is_closed,
        voter_show_names=show_short_names,
        voter_rows=rows,
    )


@bp.get("/<int:year>/voters")
def year_voters(year: int):
    return _render_year_voters(year, str(year), None, None)


@bp.get("/special/<short_name>/voters")
def special_year_voters(short_name: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404
    return _render_year_voters(
        special_year["id"],
        short_name,
        short_name,
        special_year["special_name"],
    )


@bp.get("/special/<short_name>/<show>")
def special_results(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status == "none" and not permissions.can_view_restricted:
        return render_template("error.html", error="This show has no songs"), 400

    reveal = ""
    access = show_data.status

    if permissions.can_view_restricted:
        if access == "draw":
            access = "partial"
            reveal = "unrevealed"
        elif access == "partial":
            access = "full"
            reveal = "unrevealed"
        else:
            access = "full"

    if access == "draw":
        songs = get_show_songs(_year, show, select_votes=False)
    else:
        songs = get_show_songs(_year, show, select_votes=True)

    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    participants = len(songs)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(voter_id) AS c FROM vote_set WHERE show_id = %s", (show_data.id,))
    voter_count = fetchone(cursor)["c"]
    songs.sort(reverse=True)

    off = 0
    qualifier_reveal = []

    # Build the qualifier banner whenever the partial results are actually
    # set in the database (``status == "partial"``) — including when an admin
    # viewing such a show is bumped up to full access. It is NOT shown for a
    # draw-status show an admin is merely previewing as partial, since those
    # qualifiers aren't set yet. Computed from the full sorted list before any
    # slicing/redaction below, and captured as plain values so the later
    # redaction can't blank a card. The DtF/SC groups are shuffled with the
    # same seed the qualifiers reveal uses (see ``qualifiers_scores``) so they
    # appear in reveal order.
    if show_data.status == "partial":
        dtf = show_data.dtf or 0
        sc = show_data.sc or 0
        lcg = LCG(show_data.id)
        dtf_quals = songs[:dtf]
        sc_quals = songs[dtf:dtf + sc]
        lcg.shuffle(dtf_quals)
        lcg.shuffle(sc_quals)
        qualifier_reveal = [
            {"cc": s.country.cc, "name": s.country.name,
             "variant": s.country.flag_variant, "cls": cls}
            for group, cls in ((dtf_quals, "qual-dtf"), (sc_quals, "qual-sc"))
            for s in group
        ]

    if access == "partial":
        if show_data.dtf:
            off = show_data.dtf - 1
        if show_data.sc:
            off += show_data.sc

        songs = songs[off:]
        if reveal:
            for s in songs:
                s.hidden = True

        if songs:
            if songs[0].vote_data:
                songs[0].vote_data.ro = -1
            songs[0].artist = ""
            songs[0].title = ""
            songs[0].country.name = ""
            songs[0].country.cc = "XX"
    elif access == "full" and reveal:
        if show_data.dtf:
            off = show_data.dtf - 1
        if show_data.sc:
            off += show_data.sc
        if reveal:
            for i in range(off + 1):
                songs[i].hidden = True
        off = 0

    qualifiers = show_data.dtf or 0
    sc_qualifiers = (show_data.sc or 0) + (show_data.special or 0) + qualifiers

    return render_template(
        "year/summary.html",
        hidden=reveal,
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        songs=songs,
        points=show_data.points,
        show=show,
        access=access,
        offset=off,
        qualifier_reveal=qualifier_reveal,
        other_shows=get_other_shows(_year, show),
        show_name=show_data.name,
        short_name=show_data.short_name,
        show_id=show_data.id,
        year=short_name,
        year_id=_year,
        participants=participants,
        voters=voter_count,
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.get("/special/<short_name>/<show>/detailed")
def special_detailed_results(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the detailed results yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    results: dict = {}
    cursor.execute(
        """
        SELECT username, COALESCE(country_id, 'XX') as code, country.name AS country FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        LEFT OUTER JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
        ORDER BY created_at
    """,
        (show_data.id,),
    )
    for row in cursor.fetchall():
        results[row["username"]] = row

    for song in songs:
        cursor.execute(
            """
            SELECT score, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN account ON vote_set.voter_id = account.id
            WHERE song_id = %s AND show_id = %s
            ORDER BY created_at
        """,
            (song.id, show_data.id),
        )

        for row in cursor.fetchall():
            results[row["username"]][song.id] = row["score"]

    songs.sort(reverse=True)

    qualifiers = show_data.dtf or 0
    sc_qualifiers = (show_data.sc or 0) + (show_data.special or 0) + qualifiers

    return render_template(
        "year/detailed.html",
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        other_shows=get_other_shows(_year, show),
        songs=songs,
        results=results,
        show_name=show_data.name,
        show=show,
        year=short_name,
        participants=len(songs),
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.get("/special/<short_name>/<show>/scoreboard")
def special_scoreboard(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the scoreboard yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template(
        "year/scoreboard.html",
        show=show,
        year=short_name,
        show_name=show_data.name,
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.get("/special/<short_name>/<show>/scoreboard/votes")
def special_scores(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return {"error": "You aren't allowed to access the scoreboard"}, 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return {"error": "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return {"error": "No songs found for this show."}, 404

    cursor.execute(
        """
        SELECT song_id, score AS pts, username FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        JOIN song ON vote.song_id = song.id
        WHERE vote_set.show_id = %s
        ORDER BY vote_set.created_at
    """,
        (show_data.id,),
    )
    results_raw = cursor.fetchall()
    results: dict[str, dict[int, int]] = defaultdict(dict)
    for row in results_raw:
        results[row["username"]][row["pts"]] = row["song_id"]

    sequencer: AbstractVoteSequencer
    if show_data.id < 60:
        sequencer = SuspensefulVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    elif show_data.id < 65:
        sequencer = RandomVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    else:
        sequencer = ChronologicalVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    vote_order = sequencer.get_order()

    user_songs = defaultdict(list)
    for voter_username in vote_order:
        cursor.execute(
            """
            SELECT song.id FROM song
            JOIN account ON song.submitter_id = account.id
            JOIN song_show ON song.id = song_show.song_id
            WHERE account.username = %s AND song_show.show_id = %s
        """,
            (voter_username, show_data.id),
        )
        for song_id in cursor.fetchall():
            user_songs[voter_username].append(song_id["id"])

    cursor.execute(
        """
        SELECT username, nickname, country_id AS code, country.name AS country FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
    """,
        (show_data.id,),
    )
    vote_set = cursor.fetchall()
    voter_assoc = {}
    for row in vote_set:
        voter_assoc[row["username"]] = row

    return {
        "songs": songs,
        "results": results,
        "points": show_data.points,
        "vote_order": vote_order,
        "associations": voter_assoc,
        "user_songs": user_songs,
        "penalties": _show_penalties(show_data.id),
    }


@bp.get("/special/<short_name>/<show>/predictions")
def special_predictions(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the predictions yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT prediction_set.id, account.username, prediction_set.created_at
        FROM prediction_set
        JOIN account ON prediction_set.user_id = account.id
        WHERE prediction_set.show_id = %s
        ORDER BY COALESCE(prediction_set.updated_at, prediction_set.created_at)
    """,
        (show_data.id,),
    )
    pred_sets = cursor.fetchall()

    cursor.execute(
        """
        SELECT prediction.set_id, prediction.song_id, prediction.position
        FROM prediction
        JOIN prediction_set ON prediction.set_id = prediction_set.id
        WHERE prediction_set.show_id = %s
    """,
        (show_data.id,),
    )

    pred_by_set: dict[int, dict[int, int]] = defaultdict(dict)
    for row in cursor.fetchall():
        pred_by_set[row["set_id"]][row["song_id"]] = row["position"]

    n_predictors = len(pred_sets)
    n_qualifiers = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)

    is_final = n_qualifiers <= 0
    if is_final:
        odds = _compute_winning_odds(songs, pred_by_set, n_predictors)
    else:
        odds = _compute_qualification_odds(songs, pred_by_set, n_predictors, n_qualifiers)

    predictors: dict[str, dict] = {}
    for ps in pred_sets:
        predictors[ps["username"]] = pred_by_set.get(ps["id"], {})

    pred_points: dict[int, float] = {song.id: 0.0 for song in songs}
    for set_preds in pred_by_set.values():
        for sid, pos in set_preds.items():
            if sid in pred_points:
                pred_points[sid] += 12 * (0.827 ** (pos - 1))

    pred_rank: dict[int, int] = {}
    for rank, song in enumerate(
        sorted(songs, key=lambda s: pred_points[s.id], reverse=True), start=1
    ):
        pred_rank[song.id] = rank

    real_positions: dict[int, int] = {}
    if show_data.status == "full":
        cursor.execute(
            "SELECT song_id, place FROM country_show_results WHERE show_id = %s",
            (show_data.id,),
        )
        real_positions = {row["song_id"]: row["place"] for row in cursor.fetchall()}

    if real_positions:
        songs.sort(key=lambda s: (real_positions.get(s.id) is None, real_positions.get(s.id, 0)))
    else:
        songs.sort(key=lambda s: odds[s.id], reverse=True)

    predicted_class: dict[int, str] = {}
    n_total = len(songs)
    if n_total:
        if is_final:
            predicted_class[songs[0].id] = "first"
            if n_total >= 2:
                predicted_class[songs[1].id] = "second"
            if n_total >= 3:
                predicted_class[songs[2].id] = "third"
            predicted_class[songs[-1].id] = "last"
        else:
            dtf_n = show_data.dtf or 0
            sc_n = show_data.sc or 0
            for i, song in enumerate(songs):
                if i < dtf_n:
                    predicted_class[song.id] = "direct-to-final"
                elif i < dtf_n + sc_n:
                    predicted_class[song.id] = "second-chance"
            predicted_class[songs[-1].id] = "last"

    copy_lines: list[str] = []
    for i, song in enumerate(songs, 1):
        prob = odds[song.id]
        decimal_odds = (1 / prob) if prob > 0 else float("inf")
        pct = prob * 100
        copy_lines.append(f"{i}. {song.country.name}: {decimal_odds:.2f} ({pct:.2f}%)")
    copy_text = "\n".join(copy_lines)

    show_copy = show_data.status != "full"

    predictor_scores: dict[str, int] = {}
    predictor_breakdown: dict[str, list[dict]] = {}
    predictor_penalty: dict[str, dict[int, int]] = {}
    if real_positions:
        predictor_scores, predictor_breakdown, predictor_penalty = (
            _compute_prediction_scores(real_positions, songs, predictors)
        )

    return render_template(
        "year/predictions.html",
        songs=songs,
        predictors=predictors,
        odds=odds,
        predicted_class=predicted_class,
        pred_points=pred_points,
        pred_rank=pred_rank,
        n_predictors=n_predictors,
        n_qualifiers=n_qualifiers,
        copy_text=copy_text,
        show_copy=show_copy,
        show=show,
        show_name=show_data.name,
        year=short_name,
        other_shows=get_other_shows(_year, show),
        special=short_name,
        special_name=special_year["special_name"],
        predictor_scores=predictor_scores,
        predictor_breakdown=predictor_breakdown,
        predictor_penalty=predictor_penalty,
        real_positions=real_positions,
    )


@bp.get("/special/<short_name>/<show>/song/<country_code>")
def special_song_votes_disambig(short_name: str, show: str, country_code: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    canonical = resolve_country_code(country_code.upper())
    if canonical and canonical.lower() != country_code.lower():
        return redirect(
            url_for(
                "year.special_song_votes_disambig",
                short_name=short_name,
                show=show,
                country_code=canonical.lower(),
            ),
            301,
        )

    _year = special_year["id"]
    show_data = get_show_id(show, _year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, song.title, song.native_title, song.artist,
               song.entry_number, country.name AS country_name
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s AND song.country_id = UPPER(%s)
        ORDER BY song.entry_number
    """,
        (show_data.id, country_code),
    )
    songs = cursor.fetchall()

    if not songs:
        return render_template("error.html", error="Song not found in this show"), 404

    if len(songs) == 1:
        return redirect(
            url_for(
                "year.special_song_votes",
                short_name=short_name,
                show=show,
                country_code=country_code.lower(),
                entry_number=songs[0]["entry_number"],
            )
        )

    return render_template(
        "year/special_song_disambig.html",
        songs=songs,
        country=country_code,
        country_name=songs[0]["country_name"],
        show=show,
        show_name=show_data.name,
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.get("/special/<short_name>/<show>/song/<country_code>/<int:entry_number>")
def special_song_votes(short_name: str, show: str, country_code: str, entry_number: int):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    canonical = resolve_country_code(country_code.upper())
    if canonical and canonical.lower() != country_code.lower():
        return redirect(
            url_for(
                "year.special_song_votes",
                short_name=short_name,
                show=show,
                country_code=canonical.lower(),
                entry_number=entry_number,
            ),
            301,
        )

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status not in ("full", "partial") and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the vote breakdown yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT song.id, song.title, song.artist, song.country_id,
               country.name AS country_name, country.cc3,
               song_show.running_order, song.entry_number
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
          AND song.country_id = UPPER(%s)
          AND song.entry_number = %s
    """,
        (show_data.id, country_code, entry_number),
    )
    song = cursor.fetchone()

    if not song:
        return render_template("error.html", error="Song not found in this show"), 404

    if show_data.status == "partial" and not permissions.can_view_restricted:
        qualifier_cutoff = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)
        if qualifier_cutoff > 0:
            cursor.execute(
                """
                SELECT place FROM country_show_results
                WHERE song_id = %s AND show_id = %s
            """,
                (song["id"], show_data.id),
            )
            row = cursor.fetchone()
            if row and row["place"] <= qualifier_cutoff:
                return render_template(
                    "error.html", error="The results for this song haven't been revealed yet"
                ), 400

    cursor.execute(
        """
        SELECT account.username, COALESCE(vote_set.country_id, 'XX') AS code,
               country.name AS country_name, country.cc3
        FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        LEFT OUTER JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
        ORDER BY account.username
    """,
        (show_data.id,),
    )
    all_voters = {row["username"]: row for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT account.username, vote.score
        FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        WHERE vote.song_id = %s AND vote_set.show_id = %s
    """,
        (song["id"], show_data.id),
    )
    votes_by_voter = {row["username"]: row["score"] for row in cursor.fetchall()}

    points = sorted(show_data.points, reverse=True)

    groups: dict[int, list[dict]] = defaultdict(list)
    no_points_voters: list[dict] = []

    for username, voter_info in sorted(all_voters.items(), key=lambda x: x[0].lower()):
        score = votes_by_voter.get(username, 0)
        voter_entry = {
            "username": username,
            "code": voter_info["code"],
            "cc3": voter_info.get("cc3", ""),
            "country_name": voter_info.get("country_name", ""),
        }
        if score > 0:
            groups[score].append(voter_entry)
        else:
            no_points_voters.append(voter_entry)

    point_groups = []
    total_points = 0
    for pts in points:
        voters_at_pts = groups.get(pts, [])
        voter_count = len(voters_at_pts)
        group_total = pts * voter_count
        total_points += group_total
        point_groups.append(
            {
                "points": pts,
                "voters": voters_at_pts,
                "voter_count": voter_count,
                "total": group_total,
            }
        )
    total_voters = len(all_voters)
    voters_who_gave = len(votes_by_voter)

    return render_template(
        "year/song_votes.html",
        song=song,
        show=show,
        show_name=show_data.name,
        year=short_name,
        point_groups=point_groups,
        no_points_voters=no_points_voters,
        total_points=total_points,
        total_voters=total_voters,
        voters_who_gave=voters_who_gave,
        other_shows=get_other_shows(_year, show),
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.get("/<int:year>")
def year(year: int):
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

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)
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


@bp.get("/<int:year>/<show>")
def results(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status == "none" and not permissions.can_view_restricted:
        return render_template("error.html", error="This show has no songs"), 400

    reveal = ""
    access = show_data.status

    if permissions.can_view_restricted:
        if access == "draw":
            access = "partial"
            reveal = "unrevealed"
        elif access == "partial":
            access = "full"
            reveal = "unrevealed"
        else:
            access = "full"

    if access == "draw":
        songs = get_show_songs(_year, show, select_votes=False)
    else:
        songs = get_show_songs(_year, show, select_votes=True)

    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    participants = len(songs)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(voter_id) AS c FROM vote_set WHERE show_id = %s", (show_data.id,))
    voter_count = fetchone(cursor)["c"]
    songs.sort(reverse=True)

    off = 0
    qualifier_reveal = []

    # Build the qualifier banner whenever the partial results are actually
    # set in the database (``status == "partial"``) — including when an admin
    # viewing such a show is bumped up to full access. It is NOT shown for a
    # draw-status show an admin is merely previewing as partial, since those
    # qualifiers aren't set yet. Computed from the full sorted list before any
    # slicing/redaction below, and captured as plain values so the later
    # redaction can't blank a card. The DtF/SC groups are shuffled with the
    # same seed the qualifiers reveal uses (see ``qualifiers_scores``) so they
    # appear in reveal order.
    if show_data.status == "partial":
        dtf = show_data.dtf or 0
        sc = show_data.sc or 0
        lcg = LCG(show_data.id)
        dtf_quals = songs[:dtf]
        sc_quals = songs[dtf:dtf + sc]
        lcg.shuffle(dtf_quals)
        lcg.shuffle(sc_quals)
        qualifier_reveal = [
            {"cc": s.country.cc, "name": s.country.name,
             "variant": s.country.flag_variant, "cls": cls}
            for group, cls in ((dtf_quals, "qual-dtf"), (sc_quals, "qual-sc"))
            for s in group
        ]

    if access == "partial":
        if show_data.dtf:
            off = show_data.dtf - 1
        if show_data.sc:
            off += show_data.sc

        songs = songs[off:]
        if reveal:
            for s in songs:
                s.hidden = True

        if songs:
            if songs[0].vote_data:
                songs[0].vote_data.ro = -1
            songs[0].artist = ""
            songs[0].title = ""
            songs[0].country.name = ""
            songs[0].country.cc = "XX"
    elif access == "full" and reveal:
        if show_data.dtf:
            off = show_data.dtf - 1
        if show_data.sc:
            off += show_data.sc
        if reveal:
            for i in range(off + 1):
                songs[i].hidden = True
        off = 0

    qualifiers = show_data.dtf or 0
    sc_qualifiers = (show_data.sc or 0) + (show_data.special or 0) + qualifiers

    return render_template(
        "year/summary.html",
        hidden=reveal,
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        songs=songs,
        points=show_data.points,
        show=show,
        access=access,
        offset=off,
        qualifier_reveal=qualifier_reveal,
        other_shows=get_other_shows(_year, show),
        show_name=show_data.name,
        short_name=show_data.short_name,
        show_id=show_data.id,
        year=year,
        year_id=_year,
        participants=participants,
        voters=voter_count,
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
    )


@bp.get("/<int:year>/<show>/detailed")
def detailed_results(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the detailed results yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    results: dict = {}
    cursor.execute(
        """
        SELECT username, COALESCE(country_id, 'XX') as code, country.name AS country FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        LEFT OUTER JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
        ORDER BY created_at
    """,
        (show_data.id,),
    )
    for row in cursor.fetchall():
        results[row["username"]] = row

    for song in songs:
        cursor.execute(
            """
            SELECT score, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN account ON vote_set.voter_id = account.id
            WHERE song_id = %s AND show_id = %s
            ORDER BY created_at
        """,
            (song.id, show_data.id),
        )

        for row in cursor.fetchall():
            results[row["username"]][song.id] = row["score"]

    songs.sort(reverse=True)

    qualifiers = show_data.dtf or 0
    sc_qualifiers = (show_data.sc or 0) + (show_data.special or 0) + qualifiers

    return render_template(
        "year/detailed.html",
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        other_shows=get_other_shows(_year, show),
        songs=songs,
        results=results,
        show_name=show_data.name,
        show=show,
        year=year,
        participants=len(songs),
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
    )


@bp.get("/<int:year>/<show>/song/<country_code>")
def song_votes(year: int, show: str, country_code: str):
    canonical = resolve_country_code(country_code.upper())
    if canonical and canonical.lower() != country_code.lower():
        return redirect(
            url_for("year.song_votes", year=year, show=show, country_code=canonical.lower()), 301
        )

    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status not in ("full", "partial") and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the vote breakdown yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    db = get_db()
    cursor = db.cursor()

    # Find the song for this country in this show
    cursor.execute(
        """
        SELECT song.id, song.title, song.artist, song.country_id,
               country.name AS country_name, country.cc3,
               song_show.running_order
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s AND song.country_id = UPPER(%s)
    """,
        (show_data.id, country_code),
    )
    song = cursor.fetchone()

    if not song:
        return render_template("error.html", error="Song not found in this show"), 404

    # In partial mode, block access to qualifier results
    if show_data.status == "partial" and not permissions.can_view_restricted:
        qualifier_cutoff = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)
        if qualifier_cutoff > 0:
            cursor.execute(
                """
                SELECT place FROM country_show_results
                WHERE song_id = %s AND show_id = %s
            """,
                (song["id"], show_data.id),
            )
            row = cursor.fetchone()
            if row and row["place"] <= qualifier_cutoff:
                return render_template(
                    "error.html", error="The results for this song haven't been revealed yet"
                ), 400

    # Get all voters for this show with their country associations
    cursor.execute(
        """
        SELECT account.username, COALESCE(vote_set.country_id, 'XX') AS code,
               country.name AS country_name, country.cc3
        FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        LEFT OUTER JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
        ORDER BY account.username
    """,
        (show_data.id,),
    )
    all_voters = {row["username"]: row for row in cursor.fetchall()}

    # Get all votes for this song
    cursor.execute(
        """
        SELECT account.username, vote.score
        FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        WHERE vote.song_id = %s AND vote_set.show_id = %s
    """,
        (song["id"], show_data.id),
    )
    votes_by_voter = {row["username"]: row["score"] for row in cursor.fetchall()}

    # Get the point system scores to know all possible point values
    points = sorted(show_data.points, reverse=True)

    # Group voters by points awarded
    groups: dict[int, list[dict]] = defaultdict(list)
    no_points_voters: list[dict] = []

    for username, voter_info in sorted(all_voters.items(), key=lambda x: x[0].lower()):
        score = votes_by_voter.get(username, 0)
        voter_entry = {
            "username": username,
            "code": voter_info["code"],
            "cc3": voter_info.get("cc3", ""),
            "country_name": voter_info.get("country_name", ""),
        }
        if score > 0:
            groups[score].append(voter_entry)
        else:
            no_points_voters.append(voter_entry)

    # Build ordered list of point groups
    point_groups = []
    total_points = 0
    for pts in points:
        voters_at_pts = groups.get(pts, [])
        voter_count = len(voters_at_pts)
        group_total = pts * voter_count
        total_points += group_total
        point_groups.append(
            {
                "points": pts,
                "voters": voters_at_pts,
                "voter_count": voter_count,
                "total": group_total,
            }
        )
    total_voters = len(all_voters)
    voters_who_gave = len(votes_by_voter)

    return render_template(
        "year/song_votes.html",
        song=song,
        show=show,
        show_name=show_data.name,
        year=year,
        point_groups=point_groups,
        no_points_voters=no_points_voters,
        total_points=total_points,
        total_voters=total_voters,
        voters_who_gave=voters_who_gave,
        other_shows=get_other_shows(_year, show),
    )


@bp.get("/<int:year>/<show>/scoreboard")
def scoreboard(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the scoreboard yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template("year/scoreboard.html", show=show, year=year, show_name=show_data.name)


@bp.get("/<int:year>/<show>/scoreboard/votes")
def scores(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return {"error": "You aren't allowed to access the scoreboard"}, 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return {"error": "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return {"error": "No songs found for this show."}, 404

    cursor.execute(
        """
        SELECT song_id, score AS pts, username FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        JOIN song ON vote.song_id = song.id
        WHERE vote_set.show_id = %s
        ORDER BY vote_set.created_at
    """,
        (show_data.id,),
    )
    results_raw = cursor.fetchall()
    results: dict[str, dict[int, int]] = defaultdict(dict)
    for row in results_raw:
        results[row["username"]][row["pts"]] = row["song_id"]

    sequencer: AbstractVoteSequencer
    if show_data.id < 60:
        sequencer = SuspensefulVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    elif show_data.id < 65:
        sequencer = RandomVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    else:
        sequencer = ChronologicalVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    vote_order = sequencer.get_order()

    user_songs = defaultdict(list)
    for voter_username in vote_order:
        cursor.execute(
            """
            SELECT song.id FROM song
            JOIN account ON song.submitter_id = account.id
            JOIN song_show ON song.id = song_show.song_id
            WHERE account.username = %s AND song_show.show_id = %s
        """,
            (voter_username, show_data.id),
        )
        for song_id in cursor.fetchall():
            user_songs[voter_username].append(song_id["id"])

    cursor.execute(
        """
        SELECT username, nickname, country_id AS code, country.name AS country FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
    """,
        (show_data.id,),
    )
    vote_set = cursor.fetchall()
    voter_assoc = {}
    for row in vote_set:
        voter_assoc[row["username"]] = row

    return {
        "songs": songs,
        "results": results,
        "points": show_data.points,
        "vote_order": vote_order,
        "associations": voter_assoc,
        "user_songs": user_songs,
        "penalties": _show_penalties(show_data.id),
    }


@bp.get("/<int:year>/<show>/qualifiers")
def qualifiers(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if show_data.dtf is None:
        return render_template("error.html", error="Not a semi-final."), 400

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template("error.html", error="You aren't allowed to access the qualifiers")

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template("year/qualifiers.html", show=show, year=year, show_name=show_data.name)


@bp.post("/<int:year>/<show>/qualifiers")
def qualifiers_post(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return {"error": "You aren't allowed to access the qualifiers"}, 400

    final_data = get_show_id("f", _year)
    if not final_data:
        return {"error": "Final show not found"}, 404

    sc_data = get_show_id("sc", _year)

    if show_data.short_name == "sc":
        sf_number = 9
    elif show_data.short_name.startswith("sf"):
        sf_number = int(show_data.short_name.removeprefix("sf"))
    else:
        return {"error": "Invalid semi-final show name"}, 400

    body = request.json
    if not body or not isinstance(body, dict):
        return {"error": "Invalid request body"}, 400

    action = body.get("action")
    if action != "save":
        return {"error": "Invalid action"}, 400

    db = get_db()
    cursor = db.cursor()

    final_order = body.get("dtf")
    if not final_order or not isinstance(final_order, list):
        return {"error": "Reveal order not provided"}, 400

    for i, song_id in enumerate(final_order):
        n = sf_number * 100 + (i + 1)
        add = 20 if sf_number == 9 else 1
        cursor.execute(
            """
            INSERT INTO song_show (song_id, show_id, running_order, qualifier_order)
            VALUES (%(soid)s, %(shid)s, %(ro)s, %(qo)s)
            ON CONFLICT (show_id, song_id) DO UPDATE
            SET song_id = %(soid)s,
                show_id = %(shid)s,
                running_order = %(ro)s,
                qualifier_order = %(qo)s
        """,
            {"soid": int(song_id), "shid": final_data.id, "ro": n, "qo": i + add},
        )

    if sc_data:
        second_chance_order = typing.cast(list[int], body.get("sc"))
        if second_chance_order and not isinstance(second_chance_order, list):
            return {"error": "Second chance order must be a list"}, 400

        for i, song_id in enumerate(second_chance_order):
            n = sf_number * 100 + (i + 1)
            cursor.execute(
                """
                INSERT INTO song_show (song_id, show_id, running_order, qualifier_order)
                VALUES (%(soid)s, %(shid)s, %(ro)s, %(qo)s)
                ON CONFLICT (show_id, song_id) DO UPDATE
                SET song_id = %(soid)s,
                    show_id = %(shid)s,
                    running_order = %(ro)s,
                    qualifier_order = %(qo)s
        """,
                {"soid": int(song_id), "shid": sc_data.id, "ro": n, "qo": i + 1},
            )
    db.commit()

    return {"success": True, "message": "Qualifiers saved successfully."}


@bp.get("/<int:year>/<show>/qualifiers/votes")
def qualifiers_scores(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return {"error": "You aren't allowed to access the qualifiers"}, 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return {"error": "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, song.title, song.entry_number, song_show.running_order,
               country.name AS country, country.id AS cc
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY song_show.running_order
    """,
        (show_data.id,),
    )
    countries = []
    for row in cursor.fetchall():
        val = {
            "id": row["id"],
            "title": row["title"],
            "country": row["country"],
            "cc": row["cc"],
            "entry_number": row["entry_number"],
            "points": get_votes_for_song(row["id"], show_data.id, row["running_order"]),
        }
        countries.append(val)

    countries.sort(key=lambda x: x["points"], reverse=True)

    dtf_countries = []
    for i in range(show_data.dtf):
        dtf_countries.append(countries[i])

    sc_countries = []
    for i in range(show_data.sc or 0):
        sc_countries.append(countries[show_data.dtf + i])

    countries.sort(key=lambda x: x["points"].ro)

    for c in countries:
        del c["points"]

    lcg = LCG(show_data.id)
    lcg.shuffle(dtf_countries)
    lcg.shuffle(sc_countries)

    return {
        "countries": countries,
        "reveal_order": {"dtf": dtf_countries, "sc": sc_countries},
        "dtf": show_data.dtf,
        "sc": show_data.sc or 0,
        "special": show_data.special or 0,
        # Specials may have multiple entries per country, so the reveal
        # uses the song title for disambiguation. Regular years stick with
        # country names.
        "is_special": (show_data.year or 0) < 0,
    }


# ── Special-year mirror of /qualifiers ───────────────────────────────
# These routes resolve the short_name to a year row and then delegate to
# the same logic as the regular ``/year/<int>/<show>/qualifiers`` path.
# The frontend (qualifiers.js) drives all of its requests off
# ``window.location.pathname`` so it picks up the special URL prefix
# automatically.

@bp.get("/special/<short_name>/<show>/qualifiers")
def special_qualifiers(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if show_data.dtf is None:
        return render_template("error.html", error="Not a semi-final."), 400

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template("error.html", error="You aren't allowed to access the qualifiers")

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template(
        "year/qualifiers.html",
        show=show,
        year=short_name,
        show_name=show_data.name,
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.post("/special/<short_name>/<show>/qualifiers")
def special_qualifiers_post(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404
    return qualifiers_post(special_year["id"], show)


@bp.get("/special/<short_name>/<show>/qualifiers/votes")
def special_qualifiers_scores(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404
    return qualifiers_scores(special_year["id"], show)


def _compute_qualification_odds(
    songs: list,
    pred_by_set: dict,
    n_predictors: int,
    n_qualifiers: int,
) -> dict[int, float]:
    """
    Compute qualification probability for each song in a semifinal.

    Mean-rank sigmoid model: for each song we compute a smoothed average
    rank across all predictors (with a neutral Beta-style prior pulling
    toward the middle of the leaderboard), then pass it through a logistic
    centred at the qualifier cutoff (N + 0.5). Lower-rank predictions
    (closer to 1) dominate, so a song with many 1st-place picks scores
    very highly even if a few predictors had it much lower.

    Properties of this formula:
    - Songs unanimously ranked in the top N approach 1.0.
    - Songs unanimously ranked outside the top N approach 0.0.
    - A song with mean rank exactly at the cutoff sits at 0.5.
    - 1st-place picks pull the score up much harder than mid-table picks
      pull it down (because the sigmoid saturates well above the cutoff).
    - Sum of probabilities does NOT need to equal N — they are independent
      per-song qualification probabilities.
    """
    n_songs = len(songs)
    if n_predictors == 0 or n_songs == 0 or n_qualifiers <= 0:
        return {song.id: 0.0 for song in songs}

    cutoff = n_qualifiers + 0.5
    # Temperature scales with show size so the transition zone covers
    # roughly the same fraction of the leaderboard regardless of n_songs.
    temperature = max(1.5, n_songs / 8.0)
    # Neutral prior rank — sits at the middle of the field.
    prior_rank = (n_songs + 1) / 2.0
    prior_weight = 1.0

    odds: dict[int, float] = {}
    for song in songs:
        rank_sum = prior_rank * prior_weight
        weight_sum = prior_weight
        for set_preds in pred_by_set.values():
            pos = set_preds.get(song.id, n_songs)
            rank_sum += pos
            weight_sum += 1.0
        mean_rank = rank_sum / weight_sum
        # Logistic centred at cutoff: mean_rank << cutoff → ~1, >> cutoff → ~0.
        odds[song.id] = 1.0 / (1.0 + math.exp((mean_rank - cutoff) / temperature))

    return odds


def _smooth_low_odds(
    odds: dict[int, float],
    threshold: float,
    floor: float,
) -> dict[int, float]:
    """Smooth the low-probability tail.

    Songs at or above ``threshold`` keep their raw values (rescaled
    proportionally so the total still sums to 1.0). Songs below
    ``threshold`` are remapped log-uniformly into ``[floor, threshold]``
    in their original order — preserving relative log-distances within
    the tail while preventing a long flat run pinned at exactly ``floor``.

    The result: every song's odds end up at least ``floor``, the very
    bottom songs sit just above the floor with visibly different values
    (e.g. 0.10%, 0.12%, 0.15%, …) instead of all collapsing to the same
    "1000.00" decimal odds.
    """
    n = len(odds)
    if n == 0:
        return {}
    if n * floor >= 1.0:
        # Floor higher than 1/n — degenerate; fall back to uniform.
        return {sid: 1.0 / n for sid in odds}

    above = {sid: v for sid, v in odds.items() if v >= threshold}
    below = sorted(
        ((sid, v) for sid, v in odds.items() if v < threshold),
        key=lambda x: x[1],
    )

    if not below:
        return dict(odds)

    # Pad the lower edge of the target range so the dead-last doesn't
    # always land on exactly ``floor`` — a constant pad would also look
    # suspiciously identical across shows. The pad blends two
    # data-driven signals so different finals get visibly different
    # floors:
    #   - the favourite's strength (``max_raw``): a strong consensus
    #     leader implies even outsiders shouldn't be quite at the floor.
    #   - the below-threshold tail's geometric mean: when the tail dives
    #     deep (lots of essentially-dead entries) we lift the floor more,
    #     when it sits just under threshold we lift less.
    max_raw = max(odds.values()) if odds else 0.0
    below_log_mean = sum(math.log(max(v, 1e-15)) for _, v in below) / len(below)
    below_geom_mean = math.exp(below_log_mean)
    tail_ratio = min(1.0, below_geom_mean / threshold)
    pad_factor = 1.1 + 0.7 * max_raw + 0.3 * (1.0 - tail_ratio)
    pad_factor = max(1.1, min(1.8, pad_factor))

    log_floor_target = math.log(floor * pad_factor)
    log_threshold = math.log(threshold)
    if len(below) == 1:
        spread = {below[0][0]: math.sqrt(floor * pad_factor * threshold)}
    else:
        # Map each below-threshold song's log(raw) linearly onto
        # [log(floor*1.1), log(threshold)] so within-tail log-distances
        # are preserved (just compressed into the visible band).
        b_min = max(below[0][1], 1e-15)
        b_max = max(below[-1][1], b_min * 1.0001)
        log_b_min = math.log(b_min)
        log_b_max = math.log(b_max)
        log_range = log_b_max - log_b_min
        target_range = log_threshold - log_floor_target

        spread = {}
        for sid, v in below:
            v_clamped = max(v, b_min)
            t = (math.log(v_clamped) - log_b_min) / log_range
            spread[sid] = math.exp(log_floor_target + t * target_range)

    above_sum_orig = sum(above.values())
    spread_sum = sum(spread.values())
    above_sum_target = max(0.0, 1.0 - spread_sum)

    if above_sum_orig <= 0:
        # No songs above threshold — normalize the spread to sum to 1.
        if spread_sum <= 0:
            return {sid: 1.0 / n for sid in odds}
        return {sid: v / spread_sum for sid, v in spread.items()}

    above_scale = above_sum_target / above_sum_orig
    result = {sid: v * above_scale for sid, v in above.items()}
    result.update(spread)
    return result


def _compute_winning_odds(
    songs: list,
    pred_by_set: dict,
    n_predictors: int,
) -> dict[int, float]:
    """
    Compute winning probability for each song in a final.

    Blends two complementary signals so the odds track predictor
    consensus directly without being washed out by the size of the field
    (a plain Plackett–Luce normalization shrinks the rank-1 share roughly
    like 1 / sum_r exp(-k(r-1)), which over 25+ songs makes even a clear
    favourite look weak):

    1. ``top1`` — fraction of predictors who put the song at rank 1.
       Maps consensus on the favourite directly to a win probability,
       independent of how many also-rans are in the field.
    2. ``pl`` — Plackett–Luce per-predictor softmax. Differentiates a
       song that's always 2nd/3rd from one nobody considers competitive,
       and gives some residual mass to the long tail.

    Both signals are proper distributions (sum to 1 across songs), so
    the alpha-blend is too. Tuning:
    - ``alpha`` controls how strongly consensus #1 picks dominate.
      0.7 means a unanimous favourite reaches ≈0.7 from top1 alone, with
      the PL term adding the remainder.
    - ``k`` controls the PL decay; moderate (0.4) so 2nd/3rd finishes
      still earn meaningful weight without flattening the tail to noise.
    """
    n_songs = len(songs)
    if n_predictors == 0 or n_songs == 0:
        return {song.id: 0.0 for song in songs}

    alpha = 0.7
    k = 0.4

    top1: dict[int, int] = {song.id: 0 for song in songs}
    pl_acc: dict[int, float] = {song.id: 0.0 for song in songs}

    for set_preds in pred_by_set.values():
        # Rank-1 tally
        for song in songs:
            if set_preds.get(song.id) == 1:
                top1[song.id] += 1

        # Plackett–Luce per-predictor softmax
        scores: dict[int, float] = {}
        for song in songs:
            rank = set_preds.get(song.id, n_songs)
            scores[song.id] = math.exp(-k * (rank - 1))
        total = sum(scores.values())
        if total <= 0:
            continue
        for sid, score in scores.items():
            pl_acc[sid] += score / total

    raw = {
        song.id: (
            alpha * (top1[song.id] / n_predictors)
            + (1 - alpha) * (pl_acc[song.id] / n_predictors)
        )
        for song in songs
    }

    # Keep every song's odds above 1/1000 — the bare PL tail otherwise
    # produces ugly numbers like 1/11000 for the dead-last entry — but
    # spread the low end log-uniformly into a small band [floor, 2×floor]
    # so the bottom rows show distinct values rather than a long row of
    # "0.10%". Only songs already below 0.2% get touched, so ranks above
    # the tail are essentially unchanged.
    return _smooth_low_odds(raw, threshold=0.002, floor=0.001)


def _compute_prediction_scores(
    real_positions: dict[int, int],
    songs: list,
    predictors: dict[str, dict[int, int]],
) -> tuple[dict[str, int], dict[str, list[dict]], dict[str, dict[int, int]]]:
    """Score each predictor by sum of (real_pos - predicted_pos)^2 across songs.

    Returns (scores, breakdown, penalty_by_song):
      - scores[username] = total score
      - breakdown[username] = per-song rows ordered by penalty descending
      - penalty_by_song[username][song_id] = penalty for that song
    """
    songs_by_id = {song.id: song for song in songs}

    scores: dict[str, int] = {}
    breakdown: dict[str, list[dict]] = {}
    penalty_by_song: dict[str, dict[int, int]] = {}
    for username, preds in predictors.items():
        total = 0
        rows: list[dict] = []
        per_song: dict[int, int] = {}
        for sid, predicted in preds.items():
            real = real_positions.get(sid)
            song = songs_by_id.get(sid)
            if real is None or song is None:
                continue
            penalty = (real - predicted) ** 2
            total += penalty
            per_song[sid] = penalty
            rows.append({
                "song": song,
                "predicted": predicted,
                "real": real,
                "penalty": penalty,
            })
        rows.sort(key=lambda r: r["penalty"], reverse=True)
        scores[username] = total
        breakdown[username] = rows
        penalty_by_song[username] = per_song
    return scores, breakdown, penalty_by_song


@bp.get("/<int:year>/<show>/predictions")
def show_predictions(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the predictions yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    # select_votes=True populates song.vote_data, which carries the running order
    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT prediction_set.id, account.username, prediction_set.created_at
        FROM prediction_set
        JOIN account ON prediction_set.user_id = account.id
        WHERE prediction_set.show_id = %s
        ORDER BY COALESCE(prediction_set.updated_at, prediction_set.created_at)
    """,
        (show_data.id,),
    )
    pred_sets = cursor.fetchall()

    cursor.execute(
        """
        SELECT prediction.set_id, prediction.song_id, prediction.position
        FROM prediction
        JOIN prediction_set ON prediction.set_id = prediction_set.id
        WHERE prediction_set.show_id = %s
    """,
        (show_data.id,),
    )

    pred_by_set: dict[int, dict[int, int]] = defaultdict(dict)
    for row in cursor.fetchall():
        pred_by_set[row["set_id"]][row["song_id"]] = row["position"]

    n_predictors = len(pred_sets)
    n_qualifiers = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)

    # Finals (no qualifier cutoff) get a winning-probability distribution;
    # semifinals get an independent per-song qualification probability.
    is_final = n_qualifiers <= 0
    if is_final:
        odds = _compute_winning_odds(songs, pred_by_set, n_predictors)
    else:
        odds = _compute_qualification_odds(songs, pred_by_set, n_predictors, n_qualifiers)

    # Build predictor dict ordered by submission time: {username: {song_id: position}}
    predictors: dict[str, dict] = {}
    for ps in pred_sets:
        predictors[ps["username"]] = pred_by_set.get(ps["id"], {})

    # Weighted prediction points: each predictor awards 12 * 0.827^(pos-1) points.
    # Songs are then ranked by total points to produce a predicted finishing order
    # that is independent of the qualification/winning odds.
    pred_points: dict[int, float] = {song.id: 0.0 for song in songs}
    for set_preds in pred_by_set.values():
        for sid, pos in set_preds.items():
            if sid in pred_points:
                pred_points[sid] += 12 * (0.827 ** (pos - 1))

    pred_rank: dict[int, int] = {}
    for rank, song in enumerate(
        sorted(songs, key=lambda s: pred_points[s.id], reverse=True), start=1
    ):
        pred_rank[song.id] = rank

    real_positions: dict[int, int] = {}
    if show_data.status == "full":
        cursor.execute(
            "SELECT song_id, place FROM country_show_results WHERE show_id = %s",
            (show_data.id,),
        )
        real_positions = {row["song_id"]: row["place"] for row in cursor.fetchall()}

    # Sort by real finishing place when results are public; otherwise by qualifying odds.
    if real_positions:
        songs.sort(key=lambda s: (real_positions.get(s.id) is None, real_positions.get(s.id, 0)))
    else:
        songs.sort(key=lambda s: odds[s.id], reverse=True)

    # Assign predicted-position colour classes (used as a left strip on each row).
    predicted_class: dict[int, str] = {}
    n_total = len(songs)
    if n_total:
        if is_final:
            predicted_class[songs[0].id] = "first"
            if n_total >= 2:
                predicted_class[songs[1].id] = "second"
            if n_total >= 3:
                predicted_class[songs[2].id] = "third"
            predicted_class[songs[-1].id] = "last"
        else:
            dtf_n = show_data.dtf or 0
            sc_n = show_data.sc or 0
            for i, song in enumerate(songs):
                if i < dtf_n:
                    predicted_class[song.id] = "direct-to-final"
                elif i < dtf_n + sc_n:
                    predicted_class[song.id] = "second-chance"
            predicted_class[songs[-1].id] = "last"

    # Pre-render copyable odds text
    copy_lines: list[str] = []
    for i, song in enumerate(songs, 1):
        prob = odds[song.id]
        decimal_odds = (1 / prob) if prob > 0 else float("inf")
        pct = prob * 100
        copy_lines.append(f"{i}. {song.country.name}: {decimal_odds:.2f} ({pct:.2f}%)")
    copy_text = "\n".join(copy_lines)

    # Copy box is an admin tool — hide it when the page is publicly visible
    show_copy = show_data.status != "full"

    predictor_scores: dict[str, int] = {}
    predictor_breakdown: dict[str, list[dict]] = {}
    predictor_penalty: dict[str, dict[int, int]] = {}
    if real_positions:
        predictor_scores, predictor_breakdown, predictor_penalty = (
            _compute_prediction_scores(real_positions, songs, predictors)
        )

    return render_template(
        "year/predictions.html",
        songs=songs,
        predictors=predictors,
        odds=odds,
        predicted_class=predicted_class,
        pred_points=pred_points,
        pred_rank=pred_rank,
        n_predictors=n_predictors,
        n_qualifiers=n_qualifiers,
        copy_text=copy_text,
        show_copy=show_copy,
        show=show,
        show_name=show_data.name,
        year=year,
        other_shows=get_other_shows(_year, show),
        predictor_scores=predictor_scores,
        predictor_breakdown=predictor_breakdown,
        predictor_penalty=predictor_penalty,
        real_positions=real_positions,
    )


@bp.get("/<int:year>/<show>/voters")
def show_voters(year: int, show: str):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if not permissions.can_view_restricted:
        return render_template("error.html", error="You aren't allowed to access this show"), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT username, nickname, COALESCE(country.id, 'XX') FROM vote_set
        JOIN account ON voter_id = account.id
        LEFT OUTER JOIN country ON country_id = country.id
        WHERE show_id = %s
    """,
        (show_data.id,),
    )

    return render_template("year/voters.html")


# ── Penalty management ───────────────────────────────────────────────
# Admins can dock a song its show's maximum point value when its
# submitter failed to vote in that show. The penalty is stored on
# ``song_show.penalty`` and the ``refresh_show_results`` SQL trigger
# automatically rebuilds the show's standings whenever it changes.

def _penalty_candidates(year_id: int, show_id: int) -> list[dict]:
    """Songs in ``show_id`` whose submitter has no ``vote_set`` row for
    that show. Returns the rows already merged with the current penalty
    value so the form can pre-check anyone already penalised.
    """
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id AS song_id,
               song.title,
               song.artist,
               song.entry_number,
               song.country_id AS cc,
               country.name AS country,
               country.id AS country_id,
               account.id AS submitter_id,
               account.username AS submitter,
               song_show.penalty
        FROM song_show
        JOIN song ON song.id = song_show.song_id
        JOIN country ON country.id = song.country_id
        LEFT JOIN account ON account.id = song.submitter_id
        WHERE song_show.show_id = %s
          AND song.submitter_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM vote_set
              WHERE vote_set.show_id = %s
                AND vote_set.voter_id = song.submitter_id
          )
        ORDER BY country.name, song.entry_number
        """,
        (show_id, show_id),
    )
    return cursor.fetchall()


def _show_penalties(show_id: int) -> dict[int, int]:
    """Return ``{song_id: penalty}`` for every song in the show that
    currently has a non-zero penalty applied."""
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT song_id, penalty FROM song_show WHERE show_id = %s AND penalty > 0",
        (show_id,),
    )
    return {row["song_id"]: row["penalty"] for row in cursor.fetchall()}


def _show_max_point(show_id: int) -> int:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT MAX(point.score) AS max_score
        FROM show
        JOIN point ON point.point_system_id = show.point_system_id
        WHERE show.id = %s
        """,
        (show_id,),
    )
    row = cursor.fetchone()
    return int(row["max_score"]) if row and row["max_score"] is not None else 0


def _render_penalty(show_data: ShowData, year_label: str, special_short: str | None, special_name: str | None):
    """Shared GET body for the regular and special penalty pages."""
    if not show_data.id:
        return render_template("error.html", error="Show not found"), 404
    if show_data.voting_closes is None or show_data.voting_closes > dt_now():
        return render_template(
            "error.html",
            error="Penalties can only be applied after voting has closed.",
        ), 400

    candidates = _penalty_candidates(show_data.year or 0, show_data.id)
    max_point = _show_max_point(show_data.id)

    return render_template(
        "year/penalty.html",
        year=year_label,
        special=special_short,
        special_name=special_name,
        show=show_data.short_name,
        show_name=show_data.name,
        candidates=candidates,
        max_point=max_point,
    )


def _apply_penalty(show_data: ShowData):
    """Shared POST body — checked song IDs get the max-score penalty,
    everyone else in the candidate list has theirs cleared."""
    if not show_data.id:
        return {"error": "Show not found"}, 404
    if show_data.voting_closes is None or show_data.voting_closes > dt_now():
        return {"error": "Voting hasn't closed yet"}, 400

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("song_ids", [])
    if not isinstance(raw_ids, list):
        return {"error": "song_ids must be a list"}, 400

    try:
        checked: set[int] = {int(x) for x in raw_ids}
    except (ValueError, TypeError):
        return {"error": "song_ids must be integers"}, 400

    candidates = _penalty_candidates(show_data.year or 0, show_data.id)
    candidate_ids = {row["song_id"] for row in candidates}
    # Ignore any IDs not in the eligible-list — guards against the form
    # smuggling in unrelated songs.
    checked &= candidate_ids
    cleared = candidate_ids - checked

    max_point = _show_max_point(show_data.id)

    cursor = get_db().cursor()
    if checked:
        cursor.execute(
            "UPDATE song_show SET penalty = %s "
            "WHERE show_id = %s AND song_id = ANY(%s)",
            (max_point, show_data.id, list(checked)),
        )
    if cleared:
        cursor.execute(
            "UPDATE song_show SET penalty = 0 "
            "WHERE show_id = %s AND song_id = ANY(%s)",
            (show_data.id, list(cleared)),
        )
    get_db().commit()

    return {"status": "ok", "applied": len(checked), "cleared": len(cleared)}, 200


@bp.get("/<int:year>/<show>/penalty")
def show_penalty(year: int, show: str):
    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)
    if not permissions.can_view_restricted:
        return render_template("error.html", error="Admins only."), 403
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    return _render_penalty(show_data, str(year), None, None)


@bp.post("/<int:year>/<show>/penalty")
def show_penalty_post(year: int, show: str):
    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)
    if not permissions.can_view_restricted:
        return {"error": "Admins only"}, 403
    show_data = get_show_id(show, year)
    if not show_data:
        return {"error": "Show not found"}, 404
    return _apply_penalty(show_data)


@bp.get("/special/<short_name>/<show>/penalty")
def special_show_penalty(short_name: str, show: str):
    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)
    if not permissions.can_view_restricted:
        return render_template("error.html", error="Admins only."), 403
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404
    show_data = get_show_id(show, special_year["id"])
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    return _render_penalty(
        show_data, short_name, short_name, special_year["special_name"]
    )


@bp.post("/special/<short_name>/<show>/penalty")
def special_show_penalty_post(short_name: str, show: str):
    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)
    if not permissions.can_view_restricted:
        return {"error": "Admins only"}, 403
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404
    show_data = get_show_id(show, special_year["id"])
    if not show_data:
        return {"error": "Show not found"}, 404
    return _apply_penalty(show_data)


def generate_playlist(
    show_data: ShowData, postcards: bool, include_host: bool = True
) -> tuple[str, list[str]]:
    def write(buf: io.StringIO, val: str):
        buf.write(val)
        buf.write("\n")

    def write_header(buf: io.StringIO):
        write(buf, "#EXTINF:0")
        write(buf, "#EXTVLCOPT:network-caching=3000")

    def write_country(buf: io.StringIO, cc: str, url: str) -> str | None:
        if postcards:
            write_header(buf)
            write(buf, f"https://media.world-stage.org/postcards/{cc.lower()}.mov")

        write_header(buf)
        v = None
        if "media.world-stage.org" not in url:
            v = cc

        write(buf, url or "BAD LINK REPLACE ME THIS IS A BUG")

        return v

    def show_needs_host(show_data: ShowData) -> bool:
        # Specials have no host country, so never insert a host entry for them.
        if show_data.year is None or show_data.year < 0:
            return False

        if show_data.status != "draw":
            return False

        if not show_data.short_name.startswith("sf"):
            return False

        sn = int(show_data.short_name[2])
        return sn % 2 != 0

    db = get_db()
    cursor = db.cursor()

    insert_after = -1
    host = ""
    host_link = ""
    if include_host and show_needs_host(show_data):
        cursor.execute(
            """
            SELECT LOWER(country.id) AS cc, video_link FROM year
            JOIN country ON year.host_id = country.id
            JOIN song ON song.country_id = year.host_id
            WHERE year.id = %(y)s AND song.year_id = %(y)s
        """,
            {"y": show_data.year},
        )
        data = cursor.fetchone()
        if data:
            cursor.execute(
                """
                SELECT COUNT(id) AS c FROM song_show
                WHERE show_id = %s
            """,
                (show_data.id,),
            )
            insert_after = math.ceil(fetchone(cursor)["c"] / 2) - 1
            host = data.get("cc") or ""
            host_link = data.get("video_link") or ""

    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc, video_link FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY running_order
    """,
        (show_data.id,),
    )

    output = io.StringIO(newline="\r\n")
    output.write("#EXTM3U\n")

    bad_countries = []

    for i, song in enumerate(cursor.fetchall()):
        cc = song.get("cc") or ""
        url = song.get("video_link") or ""
        b = write_country(output, cc, url)
        if b is not None:
            bad_countries.append(b)

        if i == insert_after:
            write_country(output, host, host_link)

    write_header(output)
    write(
        output,
        f"https://media.world-stage.org/recaps/{abs(show_data.year):04d}{show_data.short_name}.mov",
    )

    return output.getvalue(), bad_countries


def get_show_play_entries(
    show_data: ShowData, postcards: bool
) -> tuple[list[dict], list[str]]:
    db = get_db()
    cursor = db.cursor()

    def show_needs_host(show_data: ShowData) -> bool:
        if show_data.year is None or show_data.year < 0:
            return False
        if show_data.status != "draw":
            return False
        if not show_data.short_name.startswith("sf"):
            return False
        return int(show_data.short_name[2]) % 2 != 0

    insert_after = -1
    host_row: dict | None = None
    if show_needs_host(show_data):
        cursor.execute(
            """
            SELECT LOWER(country.id) AS cc,
                   country.name AS country,
                   song.title,
                   song.artist,
                   song.video_link AS url,
                   song.poster_link,
                   song.vtt_link
            FROM year
            JOIN country ON year.host_id = country.id
            JOIN song ON song.country_id = year.host_id
            WHERE year.id = %(y)s AND song.year_id = %(y)s
            """,
            {"y": show_data.year},
        )
        host_row = cursor.fetchone()
        if host_row:
            cursor.execute(
                "SELECT COUNT(id) AS c FROM song_show WHERE show_id = %s",
                (show_data.id,),
            )
            insert_after = math.ceil(fetchone(cursor)["c"] / 2) - 1

    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc,
               country.name AS country,
               song.title,
               song.artist,
               song.video_link AS url,
               song.poster_link,
               song.vtt_link
        FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY running_order
        """,
        (show_data.id,),
    )
    songs = cursor.fetchall()

    entries: list[dict] = []
    bad_countries: list[str] = []

    def append_song(row: dict):
        cc = (row.get("cc") or "").lower()
        url = row.get("url") or ""
        if "media.world-stage.org" not in url:
            bad_countries.append(cc)
        if postcards:
            entries.append(
                {
                    "kind": "postcard",
                    "cc": cc,
                    "country": row.get("country") or "",
                    "title": "",
                    "artist": "",
                    "url": f"https://media.world-stage.org/postcards/{cc}.mov",
                    "poster": None,
                    "vtt": None,
                }
            )
        entries.append(
            {
                "kind": "song",
                "cc": cc,
                "country": row.get("country") or "",
                "title": row.get("title") or "",
                "artist": row.get("artist") or "",
                "url": url,
                "poster": row.get("poster_link") or None,
                "vtt": row.get("vtt_link") or None,
            }
        )

    for i, song in enumerate(songs):
        append_song(song)
        if i == insert_after and host_row:
            append_song(host_row)

    entries.append(
        {
            "kind": "recap",
            "cc": "",
            "country": "",
            "title": "Recap",
            "artist": "",
            "url": (
                "https://media.world-stage.org/recaps/"
                f"{abs(show_data.year):04d}{show_data.short_name}.mov"
            ),
            "poster": None,
            "vtt": None,
        }
    )

    return entries, bad_countries


@bp.get("/<int:year>/<show>/play")
def show_play(year: int, show: str):
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    postcards = request.args.get("postcards", "false") == "true"

    entries, bad_countries = get_show_play_entries(show_data, postcards)

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if not permissions.can_view_restricted and bad_countries:
        bad_countries = sorted(set(bad_countries))
        return render_template(
            "error.html",
            error=(
                "Not all links for this show have been corrected. "
                "Please ping one of the admins. "
                f"Invalid links: {', '.join(bad_countries)}."
            ),
        )

    return render_template(
        "year/play.html",
        year=year,
        show=show,
        show_name=show_data.name,
        entries=entries,
        postcards=postcards,
        other_shows=get_other_shows(year, show),
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
        special=None,
        special_name=None,
    )


@bp.get("/special/<short_name>/<show>/play")
def special_show_play(short_name: str, show: str):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    postcards = request.args.get("postcards", "false") == "true"

    entries, bad_countries = get_show_play_entries(show_data, postcards)

    session_id = request.cookies.get("session")
    permissions = get_user_role_from_session(session_id)

    if not permissions.can_view_restricted and bad_countries:
        bad_countries = sorted(set(bad_countries))
        return render_template(
            "error.html",
            error=(
                "Not all links for this show have been corrected. "
                "Please ping one of the admins. "
                f"Invalid links: {', '.join(bad_countries)}."
            ),
        )

    return render_template(
        "year/play.html",
        year=short_name,
        show=show,
        show_name=show_data.name,
        entries=entries,
        postcards=postcards,
        other_shows=get_other_shows(_year, show),
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
        special=short_name,
        special_name=special_year["special_name"],
    )


