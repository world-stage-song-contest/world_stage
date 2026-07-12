"""Permanent re-voting for completed shows."""

from collections import defaultdict
from typing import Any

from flask import Blueprint, make_response, request, url_for

from ..db import fetchone, get_db
from ..utils import (
    get_countries,
    get_show_id,
    get_show_songs,
    get_user_songs,
    render_template,
    require_user,
)
from ..utils.types import VoteData

bp = Blueprint("revote", __name__, url_prefix="/revote")


def _resolve_revote_year(year_key: str) -> dict | None:
    cursor = get_db().cursor()
    try:
        year_id = int(year_key)
        cursor.execute("SELECT id FROM year WHERE id = %s", (year_id,))
    except ValueError:
        cursor.execute(
            "SELECT id, special_name FROM year WHERE special_short_name = %s", (year_key,)
        )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "key": year_key,
        "label": row.get("special_name") or str(row["id"]),
    }


def _other_revote_shows(year_id: int, current_show: str) -> list[dict]:
    """Eligible shows from the current revote year, excluding the current show."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show.short_name
        FROM show
        WHERE show.year_id = %s
          AND show.revote_eligible_at IS NOT NULL
        ORDER BY show.id
        """,
        (year_id,),
    )
    shows = []
    for row in cursor.fetchall():
        if row["short_name"] != current_show:
            shows.append({"short_name": row["short_name"]})
    return shows


def _original_results_url(year_id: int, short_name: str) -> str:
    cursor = get_db().cursor()
    cursor.execute("SELECT special_short_name FROM year WHERE id = %s", (year_id,))
    special_short_name = cursor.fetchone()["special_short_name"]
    if special_short_name:
        return url_for(
            "year.special_results", short_name=special_short_name, show=short_name
        )
    return url_for("year.results", year=year_id, show=short_name)


def _eligible_show(year_key: str, short_name: str):
    year = _resolve_revote_year(year_key)
    if not year:
        return None, None, (render_template("error.html", error="Year not found"), 404)

    show = get_show_id(short_name, year["id"])
    if not show or not show.id:
        return None, None, (render_template("error.html", error="Show not found"), 404)

    cursor = get_db().cursor()
    cursor.execute("SELECT revote_eligible_at FROM show WHERE id = %s", (show.id,))
    row = cursor.fetchone()
    if not row or not row["revote_eligible_at"]:
        return None, None, (
            render_template("error.html", error="Re-voting is not available for this show"),
            400,
        )
    return show, year, None


def _existing_ballot(cursor, voter_id: int, show_id: int) -> tuple[dict | None, str | None]:
    """Prefer a revote, falling back to the preserved official ballot."""
    cursor.execute(
        """
        SELECT id, nickname, country_id, result_mode
        FROM vote_set
        WHERE voter_id = %s AND show_id = %s
        ORDER BY (result_mode = 'revote') DESC
        LIMIT 1
        """,
        (voter_id, show_id),
    )
    ballot = cursor.fetchone()
    return ballot, ballot["result_mode"] if ballot else None


def _ballot_selection(cursor, ballot: dict | None) -> dict[int, dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = defaultdict(dict)
    if not ballot:
        return selected
    cursor.execute(
        """
        SELECT vote.song_id, vote.score, country.id AS cc
        FROM vote
        JOIN song ON vote.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE vote.vote_set_id = %s
        """,
        (ballot["id"],),
    )
    for row in cursor.fetchall():
        selected[row["score"]] = {"sid": row["song_id"], "cc": row["cc"]}
    return selected


def _save_revote(
    voter_id: int,
    show_id: int,
    nickname: str | None,
    country_id: str | None,
    votes: dict[int, int],
) -> str:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO vote_set (voter_id, show_id, country_id, nickname, result_mode)
        VALUES (%s, %s, %s, %s, 'revote')
        ON CONFLICT (voter_id, show_id, result_mode) DO UPDATE
        SET country_id = EXCLUDED.country_id, nickname = EXCLUDED.nickname
        RETURNING id, (xmax = 0) AS inserted
        """,
        (voter_id, show_id, country_id or "XX", nickname),
    )
    row = fetchone(cursor)
    cursor.execute("DELETE FROM vote WHERE vote_set_id = %s", (row["id"],))
    cursor.executemany(
        "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
        [(row["id"], song_id, score) for score, song_id in votes.items()],
    )
    db.commit()
    return "added" if row["inserted"] else "updated"


@bp.get("/")
def index():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show.show_name AS name, show.short_name, show.year_id AS year,
               year.special_name, year.special_short_name
        FROM show
        JOIN year ON year.id = show.year_id
        WHERE show.revote_eligible_at IS NOT NULL
        ORDER BY (show.year_id < 0), show.year_id DESC, show.id
        """
    )
    years: dict[int, dict] = {}
    specials: dict[str, dict] = {}
    for row in cursor.fetchall():
        show = {"name": row["name"], "short_name": row["short_name"]}
        if row["special_short_name"]:
            special = specials.setdefault(
                row["special_short_name"],
                {"name": row["special_name"], "key": row["special_short_name"], "shows": []},
            )
            special["shows"].append(show)
        else:
            year = years.setdefault(
                row["year"], {"year": row["year"], "key": str(row["year"]), "shows": []}
            )
            year["shows"].append(show)
    return render_template(
        "revote/index.html",
        year_sections=list(years.values()),
        special_sections=list(specials.values()),
    )


@bp.get("/<year>")
def year(year: str):
    year_data = _resolve_revote_year(year)
    if not year_data:
        return render_template("error.html", error="Year not found"), 404

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show_name AS name, short_name
        FROM show
        WHERE year_id = %s AND revote_eligible_at IS NOT NULL
        ORDER BY id
        """,
        (year_data["id"],),
    )
    shows = cursor.fetchall()
    return render_template("revote/year.html", year=year_data, shows=shows)


@bp.get("/<year>/<show>/vote")
@require_user(message="Please log in to revote")
def vote(year: str, show: str, user: tuple[int, str]):
    show_data, revote_year, error = _eligible_show(year, show)
    if error:
        return error

    voter_id, username = user
    cursor = get_db().cursor()
    ballot, ballot_mode = _existing_ballot(cursor, voter_id, show_data.id)
    user_songs = get_user_songs(voter_id, show_data.year)
    countries = list({song.country.cc: song.country for song in user_songs}.values())
    if not countries:
        countries = get_countries()
    cursor.execute(
        "SELECT COUNT(*) AS count FROM vote_set WHERE show_id = %s AND result_mode = 'revote'",
        (show_data.id,),
    )
    revote_count = fetchone(cursor)["count"]
    selected = _ballot_selection(cursor, ballot)

    return render_template(
        "vote/vote.html",
        songs=[
            song
            for song in get_show_songs(show_data.year, show_data.short_name) or []
            if song.submitter_id != voter_id
        ],
        points=show_data.points,
        selected=selected,
        username=username,
        nickname=ballot["nickname"] if ballot else None,
        year=show_data.year,
        show_name=show_data.name,
        show=show,
        short_name=show_data.short_name,
        selected_country=ballot["country_id"] if ballot else None,
        countries=countries,
        vote_count=revote_count,
        is_revote=True,
        ballot_mode=ballot_mode,
        other_revote_shows=_other_revote_shows(show_data.year, show),
        revote_year=revote_year["key"],
        original_results_url=_original_results_url(show_data.year, show_data.short_name),
    )


@bp.get("/<year>/<show>/song/<int:song_id>")
def song_votes(year: str, show: str, song_id: int):
    show_data, revote_year, error = _eligible_show(year, show)
    if error:
        return error

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id, song.title, song.artist, song.country_id,
               country.name AS country_name
        FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON country.id = song.country_id
        WHERE song_show.show_id = %s AND song.id = %s
        """,
        (show_data.id, song_id),
    )
    song = cursor.fetchone()
    if not song:
        return render_template("error.html", error="Song not found in this show"), 404

    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM vote_set WHERE show_id = %s AND result_mode = 'revote')"
        " AS has_revotes",
        (show_data.id,),
    )
    has_revotes = fetchone(cursor)["has_revotes"]

    cursor.execute(
        """
        SELECT vote_set.voter_id, account.username,
               COALESCE(vote_set.country_id, 'XX') AS code,
               country.name AS country_name, vote_set.result_mode
        FROM vote_set
        JOIN account ON account.id = vote_set.voter_id
        LEFT JOIN country ON country.id = vote_set.country_id
        WHERE vote_set.show_id = %s AND vote_set.result_mode IN ('official', 'revote')
        ORDER BY account.username, vote_set.result_mode
        """,
        (show_data.id,),
    )
    voters_by_id = {}
    for voter in cursor.fetchall():
        # A Revote ballot supersedes this person's original ballot, including
        # its displayed country, while original-only voters remain visible.
        if voter["voter_id"] not in voters_by_id or voter["result_mode"] == "revote":
            voters_by_id[voter["voter_id"]] = voter
    voters = sorted(voters_by_id.values(), key=lambda voter: voter["username"].lower())
    revote_voter_ids = {
        voter["voter_id"] for voter in voters_by_id.values() if voter["result_mode"] == "revote"
    }

    def scores_for_mode(mode: str) -> dict[int, int]:
        cursor.execute(
            """
            SELECT vote_set.voter_id, vote.score
            FROM vote
            JOIN vote_set ON vote_set.id = vote.vote_set_id
            WHERE vote.song_id = %s AND vote_set.show_id = %s
              AND vote_set.result_mode = %s
            """,
            (song_id, show_data.id, mode),
        )
        return {row["voter_id"]: row["score"] for row in cursor.fetchall()}

    original_scores = scores_for_mode("official")
    revote_scores = scores_for_mode("revote") if has_revotes else {}
    scores = original_scores | revote_scores
    groups: dict[int, list[dict]] = defaultdict(list)
    no_points_voters: list[dict] = []
    for voter in voters:
        score = scores.get(voter["voter_id"], 0)
        voter_entry = {
            "username": voter["username"],
            "code": voter["code"],
            "country_name": voter["country_name"] or "",
            "changed": (
                voter["voter_id"] in revote_voter_ids
                and score != original_scores.get(voter["voter_id"], 0)
            ),
        }
        if score:
            groups[score].append(voter_entry)
        else:
            no_points_voters.append(voter_entry)

    points = sorted(show_data.points, reverse=True)
    point_groups = [
        {
            "points": points_value,
            "voters": groups.get(points_value, []),
            "voter_count": len(groups.get(points_value, [])),
            "total": points_value * len(groups.get(points_value, [])),
        }
        for points_value in points
    ]

    return render_template(
        "year/song_votes.html",
        song=song,
        show=show,
        show_name=show_data.name,
        year=show_data.year,
        point_groups=point_groups,
        no_points_voters=no_points_voters,
        total_points=sum(score * len(voters) for score, voters in groups.items()),
        total_voters=len(voters),
        voters_who_gave=len(scores),
        other_revote_shows=_other_revote_shows(show_data.year, show),
        revote_year=revote_year["key"],
        original_results_url=_original_results_url(show_data.year, show_data.short_name),
        showing_original=not has_revotes,
        is_revote=True,
    )


@bp.get("/<year>/<show>")
def results(year: str, show: str):
    show_data, revote_year, error = _eligible_show(year, show)
    if error:
        return error

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song_id, place, total_points, total_countries, entry_status
        FROM country_show_results
        WHERE show_id = %s AND result_mode = 'official'
        """,
        (show_data.id,),
    )
    original_results = {row["song_id"]: row for row in cursor.fetchall()}
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM country_show_results
            WHERE show_id = %s AND result_mode = 'revote'
        ) AS has_results
        """,
        (show_data.id,),
    )
    has_results = fetchone(cursor)["has_results"]
    revoters_only = request.args.get("revoters_only") == "true"
    result_mode = "revote" if has_results else "official"
    songs = get_show_songs(
        show_data.year,
        show_data.short_name,
        select_votes=True,
        result_mode=result_mode,
    ) or []
    if revoters_only:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM vote_set WHERE show_id = %s AND result_mode = 'revote'",
            (show_data.id,),
        )
        revote_voters = fetchone(cursor)["count"]
        cursor.execute(
            """
            SELECT vote.song_id, vote.score, COUNT(*) AS count
            FROM vote
            JOIN vote_set ON vote_set.id = vote.vote_set_id
            WHERE vote_set.show_id = %s AND vote_set.result_mode = 'revote'
            GROUP BY vote.song_id, vote.score
            """,
            (show_data.id,),
        )
        scores: dict[int, dict[int, int]] = defaultdict(dict)
        for row in cursor.fetchall():
            scores[row["song_id"]][row["score"]] = row["count"]
        for song in songs:
            distribution = scores.get(song.id, {})
            penalty = song.vote_data.penalty if song.vote_data else 0
            data = VoteData(
                ro=song.vote_data.ro if song.vote_data else 0,
                total_votes=sum(distribution.values()),
                max_pts=max(show_data.points, default=0),
                show_voters=revote_voters,
            )
            data.count = sum(distribution.values())
            data.pts.update(distribution)
            data.penalty = penalty
            data.sum = max(sum(score * count for score, count in distribution.items()) - penalty, 0)
            song.vote_data = data
    songs.sort(reverse=True)

    return render_template(
        "revote/results.html",
        show=show,
        show_name=show_data.name,
        year=show_data.year,
        songs=songs,
        points=show_data.points,
        qualifiers=show_data.dtf or 0,
        sc_qualifiers=(show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0),
        participants=len(songs),
        original_results=original_results,
        voters=songs[0].vote_data.show_voters if songs and songs[0].vote_data else 0,
        showing_original=not has_results and not revoters_only,
        revoters_only=revoters_only,
        other_revote_shows=_other_revote_shows(show_data.year, show),
        revote_year=revote_year["key"],
        original_results_url=_original_results_url(show_data.year, show_data.short_name),
    )


@bp.get("/<year>/<show>/detailed")
def detailed_results(year: str, show: str):
    show_data, revote_year, error = _eligible_show(year, show)
    if error:
        return error

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song_id, entry_status
        FROM country_show_results
        WHERE show_id = %s AND result_mode = 'official'
        """,
        (show_data.id,),
    )
    original_results = {row["song_id"]: row for row in cursor.fetchall()}
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM country_show_results
            WHERE show_id = %s AND result_mode = 'revote'
        ) AS has_results
        """,
        (show_data.id,),
    )
    has_results = fetchone(cursor)["has_results"]
    songs = get_show_songs(
        show_data.year,
        show_data.short_name,
        select_votes=True,
        result_mode="revote" if has_results else "official",
    ) or []
    songs.sort(reverse=True)

    def ballots_for_mode(result_mode: str) -> list[dict]:
        cursor.execute(
            """
            SELECT vote_set.id, vote_set.voter_id, account.username,
                   COALESCE(vote_set.country_id, 'XX') AS code,
                   country.name AS country
            FROM vote_set
            JOIN account ON account.id = vote_set.voter_id
            LEFT JOIN country ON country.id = vote_set.country_id
            WHERE vote_set.show_id = %s AND vote_set.result_mode = %s
            ORDER BY vote_set.created_at, vote_set.id
            """,
            (show_data.id, result_mode),
        )
        ballots = [dict(row) for row in cursor.fetchall()]
        if not ballots:
            return ballots
        ballot_by_id = {ballot["id"]: ballot for ballot in ballots}
        cursor.execute(
            """
            SELECT vote.vote_set_id, vote.song_id, vote.score
            FROM vote
            JOIN vote_set ON vote_set.id = vote.vote_set_id
            WHERE vote_set.show_id = %s AND vote_set.result_mode = %s
            """,
            (show_data.id, result_mode),
        )
        for row in cursor.fetchall():
            ballot_by_id[row["vote_set_id"]][row["song_id"]] = row["score"]
        return ballots

    revote_ballots = ballots_for_mode("revote")
    revote_voter_ids = {ballot["voter_id"] for ballot in revote_ballots}
    original_ballots = [
        ballot
        for ballot in ballots_for_mode("official")
        if ballot["voter_id"] not in revote_voter_ids
    ]

    return render_template(
        "year/detailed.html",
        show=show,
        show_name=show_data.name,
        year=show_data.year,
        songs=songs,
        participants=len(songs),
        ballot_groups=[
            {"ballots": revote_ballots, "revote": True},
            {"ballots": original_ballots, "revote": False},
        ],
        original_results=original_results,
        other_revote_shows=_other_revote_shows(show_data.year, show),
        revote_year=revote_year["key"],
        original_results_url=_original_results_url(show_data.year, show_data.short_name),
        is_revote=True,
    )


@bp.post("/<year>/<show>/vote")
@require_user(message="Please log in to revote")
def vote_post(year: str, show: str, user: tuple[int, str]):
    show_data, revote_year, error = _eligible_show(year, show)
    if error:
        return error

    voter_id, username = user
    songs = get_show_songs(show_data.year, show_data.short_name) or []
    songs_by_id = {song.id: song for song in songs}
    selectable_songs = [song for song in songs if song.submitter_id != voter_id]
    user_songs = get_user_songs(voter_id, show_data.year)
    user_song_ids = {song.id for song in user_songs}
    countries = list({song.country.cc: song.country for song in user_songs}.values())
    if not countries:
        countries = get_countries()
    allowed_country_ids = {country.cc for country in countries}
    nickname = request.form.get("nickname", "").strip() or None
    country_id = request.form.get("country") or None
    errors: list[str] = []
    invalid: list[int] = []
    votes: dict[int, int] = {}

    if country_id and country_id not in allowed_country_ids:
        errors.append("You can only vote using one of your submitted countries.")
        country_id = None

    for point in show_data.points:
        value = request.form.get(f"pts-{point}")
        try:
            song_id = int(value) if value else None
        except ValueError:
            song_id = None
        if song_id is None:
            errors.append(f"Missing vote for {point} points.")
            invalid.append(point)
        elif song_id not in songs_by_id:
            errors.append(f"Invalid song for {point} points.")
            invalid.append(point)
        elif song_id in user_song_ids:
            errors.append(f"You cannot vote for your own song ({point} points).")
            invalid.append(point)
        else:
            votes[point] = song_id

    duplicate_points = [
        point for point, song_id in votes.items() if list(votes.values()).count(song_id) > 1
    ]
    if duplicate_points:
        errors.append("A song can only receive one score.")
        invalid.extend(duplicate_points)

    if not errors:
        action = _save_revote(voter_id, show_data.id, nickname, country_id, votes)
        return make_response(
            render_template(
                "vote/success.html", action=action, what="revote", what_act="revoting"
            )
        )

    selected: dict[int, dict[str, Any]] = defaultdict(dict)
    for point, song_id in votes.items():
        selected[point] = {"sid": song_id, "cc": songs_by_id[song_id].country.cc}
    return render_template(
        "vote/vote.html",
        songs=selectable_songs,
        points=show_data.points,
        errors=errors,
        selected=selected,
        invalid=invalid,
        username=username,
        nickname=nickname,
        year=show_data.year,
        show_name=show_data.name,
        show=show,
        short_name=show_data.short_name,
        selected_country=country_id,
        countries=countries,
        vote_count=0,
        is_revote=True,
        other_revote_shows=_other_revote_shows(show_data.year, show),
        revote_year=revote_year["key"],
        original_results_url=_original_results_url(show_data.year, show_data.short_name),
    )
