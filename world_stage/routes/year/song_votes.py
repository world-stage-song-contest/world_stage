from collections import defaultdict

from flask import redirect, url_for

from ...db import get_db
from ...utils import (
    UserPermissions,
    dt_now,
    get_show_id,
    render_template,
    resolve_country_code,
    with_permissions,
)
from .common import bp, get_other_shows, resolve_special


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
@with_permissions
def special_song_votes(
    short_name: str, show: str, country_code: str, entry_number: int, permissions: UserPermissions
):
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
                WHERE song_id = %s AND show_id = %s AND result_mode = 'official'
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
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
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
          AND vote_set.result_mode = 'official'
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

@bp.get("/<int:year>/<show>/song/<country_code>")
@with_permissions
def song_votes(year: int, show: str, country_code: str, permissions: UserPermissions):
    canonical = resolve_country_code(country_code.upper())
    if canonical and canonical.lower() != country_code.lower():
        return redirect(
            url_for("year.song_votes", year=year, show=show, country_code=canonical.lower()), 301
        )

    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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
                WHERE song_id = %s AND show_id = %s AND result_mode = 'official'
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
        WHERE vote_set.show_id = %s AND vote_set.result_mode = 'official'
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
          AND vote_set.result_mode = 'official'
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
