from collections import defaultdict

from ...db import get_db
from ...utils import (
    AbstractVoteSequencer,
    ChronologicalVoteSequencer,
    RandomVoteSequencer,
    SuspensefulVoteSequencer,
    UserPermissions,
    can_manage_show,
    dt_now,
    get_show_id,
    get_show_result_entries,
    render_template,
    with_auth,
)
from .common import bp, resolve_special
from .penalty import _show_penalties
from .themes import scoreboard_theme


def _scoreboard_theme(year_id: int) -> dict:
    """Backward-compatible wrapper for scoreboard theme resolution."""
    return scoreboard_theme(year_id)


def _scoreboard_data(show_data, songs) -> dict:
    """Build scoreboard payload with a fixed number of bulk queries."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT vote.song_id, vote.score AS pts, account.username
        FROM vote
        JOIN vote_set ON vote_set.id = vote.vote_set_id
        JOIN account ON account.id = vote_set.voter_id
        WHERE vote_set.show_id = %s
          AND vote_set.result_mode = 'official'
        ORDER BY vote_set.created_at
        """,
        (show_data.id,),
    )
    results: dict[str, dict[int, int]] = defaultdict(dict)
    for row in cursor.fetchall():
        results[row["username"]][row["pts"]] = row["song_id"]

    sequencer: AbstractVoteSequencer
    if show_data.id < 60:
        sequencer = SuspensefulVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    elif show_data.id < 65:
        sequencer = RandomVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    else:
        sequencer = ChronologicalVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    vote_order = sequencer.get_order()

    cursor.execute(
        """
        SELECT account.username, song.id AS song_id
        FROM song_show
        JOIN song ON song.id = song_show.song_id
        JOIN LATERAL (
            SELECT song_data.submitter_id, song_data.title, song_data.artist
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
        JOIN account ON account.id = data.submitter_id
        WHERE song_show.show_id = %s
          AND data.title IS NOT NULL
          AND data.artist IS NOT NULL
        ORDER BY song_show.running_order, song_show.id
        """,
        (show_data.id,),
    )
    user_songs: defaultdict[str, list[int]] = defaultdict(list)
    for row in cursor.fetchall():
        user_songs[row["username"]].append(row["song_id"])

    cursor.execute(
        """
        SELECT account.username, vote_set.nickname,
               vote_set.country_id AS code, country.name AS country
        FROM vote_set
        JOIN account ON account.id = vote_set.voter_id
        JOIN country ON country.id = vote_set.country_id
        WHERE vote_set.show_id = %s
          AND vote_set.result_mode = 'official'
        """,
        (show_data.id,),
    )
    voter_assoc = {row["username"]: row for row in cursor.fetchall()}

    return {
        "songs": songs,
        "results": results,
        "points": show_data.points,
        "vote_order": vote_order,
        "associations": voter_assoc,
        "user_songs": user_songs,
        "penalties": _show_penalties(show_data.id),
    }


@bp.get("/special/<short_name>/<show>/scoreboard")
@with_auth
def special_scoreboard(short_name: str, show: str, user, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return render_template(
            "error.html", error="You aren't allowed to access the scoreboard yet"
        ), 400

    if show_data.voting_closes and show_data.voting_closes > dt_now() and not elevated:
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template(
        "year/scoreboard.html",
        show=show,
        year=short_name,
        show_name=show_data.name,
        special=short_name,
        special_name=special_year["special_name"],
        **_scoreboard_theme(_year),
    )


@bp.get("/special/<short_name>/<show>/scoreboard/votes")
@with_auth
def special_scores(short_name: str, show: str, user, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return {"error": "You aren't allowed to access the scoreboard"}, 400

    if show_data.voting_closes and show_data.voting_closes > dt_now() and not elevated:
        return {"error": "Voting hasn't closed yet."}, 400

    songs = get_show_result_entries(_year, show)
    if not songs:
        return {"error": "No songs found for this show."}, 404

    return _scoreboard_data(show_data, songs)


@bp.get("/<int:year>/<show>/scoreboard")
@with_auth
def scoreboard(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return render_template(
            "error.html", error="You aren't allowed to access the scoreboard yet"
        ), 400

    if show_data.voting_closes and show_data.voting_closes > dt_now() and not elevated:
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template(
        "year/scoreboard.html",
        show=show,
        year=year,
        show_name=show_data.name,
        **_scoreboard_theme(_year),
    )


@bp.get("/<int:year>/<show>/scoreboard/votes")
@with_auth
def scores(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return {"error": "You aren't allowed to access the scoreboard"}, 400

    if show_data.voting_closes and show_data.voting_closes > dt_now() and not elevated:
        return {"error": "Voting hasn't closed yet."}, 400

    songs = get_show_result_entries(_year, show)
    if not songs:
        return {"error": "No songs found for this show."}, 404

    return _scoreboard_data(show_data, songs)
