from collections import defaultdict

from ...db import get_db
from ...utils import (
    AbstractVoteSequencer,
    ChronologicalVoteSequencer,
    RandomVoteSequencer,
    SuspensefulVoteSequencer,
    UserPermissions,
    dt_now,
    get_show_id,
    get_show_songs,
    render_template,
    with_permissions,
)
from .common import bp, resolve_special
from .penalty import _show_penalties


@bp.get("/special/<short_name>/<show>/scoreboard")
@with_permissions
def special_scoreboard(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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
@with_permissions
def special_scores(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return {"error": "Special not found"}, 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

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
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
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
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
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

@bp.get("/<int:year>/<show>/scoreboard")
@with_permissions
def scoreboard(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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
@with_permissions
def scores(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

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
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
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
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
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
