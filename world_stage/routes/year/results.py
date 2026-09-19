from ...db import fetchone, get_db
from ...utils import (
    UserPermissions,
    can_manage_show,
    dt_now,
    get_show_id,
    get_show_lineup,
    get_show_result_entries,
    render_template,
    with_auth,
)
from ...utils.voting import result_points
from .common import bp, resolve_special


def _get_detailed_votes(show_id: int) -> tuple[list[dict], dict[tuple[int, int], int]]:
    """Fetch voter metadata and the complete score matrix for one show."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT vote_set.voter_id, account.username,
               COALESCE(vote_set.country_id, 'XX') AS code,
               country.name AS country,
               vote.song_id, vote.score
        FROM vote_set
        JOIN account ON account.id = vote_set.voter_id
        LEFT JOIN country ON country.id = vote_set.country_id
        LEFT JOIN vote ON vote.vote_set_id = vote_set.id
        WHERE vote_set.show_id = %s
          AND vote_set.result_mode = 'official'
        ORDER BY vote_set.created_at, vote_set.id
        """,
        (show_id,),
    )

    voters = []
    seen_voters: set[int] = set()
    scores: dict[tuple[int, int], int] = {}
    for row in cursor.fetchall():
        voter_id = row["voter_id"]
        if voter_id not in seen_voters:
            seen_voters.add(voter_id)
            voters.append(
                {
                    "id": voter_id,
                    "username": row["username"],
                    "code": row["code"],
                    "country": row["country"],
                }
            )
        if row["song_id"] is not None:
            scores[(voter_id, row["song_id"])] = row["score"]
    return voters, scores


def _qualification_groups(show_data, songs):
    """Return actual saved advancements, or the ranking-derived preview."""
    derived = []
    offset = 0
    for progression in show_data.progressions:
        count = progression["qualifier_count"]
        derived.append([(song, False) for song in songs[offset : offset + count]])
        offset += count

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT target_show_id, song_id, is_special
        FROM show_qualifier
        WHERE source_show_id = %s
        ORDER BY target_show_id, qualifier_order
        """,
        (show_data.id,),
    )
    saved = cursor.fetchall()
    songs_by_id = {song.id: song for song in songs}
    if saved:
        by_target: dict[int, list[tuple[object, bool]]] = {}
        for qualifier in saved:
            song = songs_by_id.get(qualifier["song_id"])
            if song:
                by_target.setdefault(qualifier["target_show_id"], []).append(
                    (song, qualifier["is_special"])
                )
        return [
            by_target.get(progression["target_show_id"], derived[index])
            for index, progression in enumerate(show_data.progressions)
        ]
    return derived


def _prepare_qualification_results(show_data, songs, access: str, reveal: str):
    result_places = {song.id: place for place, song in enumerate(songs, 1)}
    groups = _qualification_groups(show_data, songs)
    qualifiers = [song for group in groups for song, _special in group]
    qualifier_ids = {song.id for song in qualifiers}
    qualifier_reveal = []

    if show_data.status == "partial":
        for index, group in enumerate(groups):
            qualifier_reveal.extend(
                {
                    "cc": song.country.cc,
                    "name": song.country.name,
                    "variant": song.country.flag_variant,
                    "cls": "qual-dtf" if index == 0 else "qual-sc",
                    "special": special,
                }
                for song, special in group
            )

    if access == "partial":
        placeholder = max(qualifiers, key=lambda song: result_places[song.id], default=None)
        songs = [song for song in songs if song.id not in qualifier_ids or song is placeholder]
        if reveal:
            for song in songs:
                song.hidden = True
        if placeholder:
            if placeholder.vote_data:
                placeholder.vote_data.ro = -1
            placeholder.artist = ""
            placeholder.artists = []
            placeholder.title = ""
            placeholder.country.name = ""
            placeholder.country.cc = "XX"
    elif access == "full" and reveal:
        for song in qualifiers:
            song.hidden = True

    return songs, qualifier_reveal, result_places


@bp.get("/special/<short_name>/<show>")
@with_auth
def special_results(short_name: str, show: str, user, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status == "none" and not elevated:
        return render_template("error.html", error="This show has no songs"), 400

    reveal = ""
    access = show_data.status

    if elevated:
        if access == "draw":
            access = "partial"
            reveal = "unrevealed"
        elif access == "partial":
            access = "full"
            reveal = "unrevealed"
        else:
            access = "full"

    if access == "draw":
        songs = get_show_lineup(_year, show)
    else:
        songs = get_show_result_entries(_year, show)

    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    participants = len(songs)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(voter_id) AS c FROM vote_set WHERE show_id = %s AND result_mode = 'official'",
        (show_data.id,),
    )
    voter_count = fetchone(cursor)["c"]
    songs.sort(reverse=True)

    songs, qualifier_reveal, result_places = _prepare_qualification_results(
        show_data, songs, access, reveal
    )

    qualifiers = show_data.primary_qualifiers
    sc_qualifiers = show_data.total_qualifiers

    return render_template(
        "year/summary.html",
        hidden=reveal,
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        songs=songs,
        points=result_points(show_data),
        show=show,
        access=access,
        host_available=show_data.status == "full",
        offset=0,
        result_places=result_places,
        qualifier_reveal=qualifier_reveal,
        show_name=show_data.name,
        short_name=show_data.short_name,
        show_id=show_data.id,
        year=short_name,
        year_id=_year,
        participants=participants,
        voters=voter_count,
        national_final_name=show_data.national_final_name,
        national_final_short_name=show_data.national_final_short_name,
        special=short_name,
        special_name=special_year["special_name"],
    )


@bp.get("/special/<short_name>/<show>/detailed")
@with_auth
def special_detailed_results(short_name: str, show: str, user, permissions: UserPermissions):
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
            "error.html", error="You aren't allowed to access the detailed results yet"
        ), 400

    if show_data.voting_closes and show_data.voting_closes > dt_now() and not elevated:
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    songs = get_show_result_entries(_year, show)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    voters, scores = _get_detailed_votes(show_data.id)

    songs.sort(reverse=True)

    qualifiers = show_data.primary_qualifiers
    sc_qualifiers = show_data.total_qualifiers

    return render_template(
        "year/detailed.html",
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        songs=songs,
        voters=voters,
        scores=scores,
        show_name=show_data.name,
        show=show,
        year=short_name,
        participants=len(songs),
        special=short_name,
        special_name=special_year["special_name"],
        national_final_name=show_data.national_final_name,
        national_final_short_name=show_data.national_final_short_name,
    )


@bp.get("/<int:year>/<show>")
@with_auth
def results(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status == "none" and not elevated:
        return render_template("error.html", error="This show has no songs"), 400

    reveal = ""
    access = show_data.status

    if elevated:
        if access == "draw":
            access = "partial"
            reveal = "unrevealed"
        elif access == "partial":
            access = "full"
            reveal = "unrevealed"
        else:
            access = "full"

    if access == "draw":
        songs = get_show_lineup(_year, show)
    else:
        songs = get_show_result_entries(_year, show)

    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    participants = len(songs)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(voter_id) AS c FROM vote_set WHERE show_id = %s AND result_mode = 'official'",
        (show_data.id,),
    )
    voter_count = fetchone(cursor)["c"]
    songs.sort(reverse=True)

    songs, qualifier_reveal, result_places = _prepare_qualification_results(
        show_data, songs, access, reveal
    )

    qualifiers = show_data.primary_qualifiers
    sc_qualifiers = show_data.total_qualifiers

    return render_template(
        "year/summary.html",
        hidden=reveal,
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        songs=songs,
        points=result_points(show_data),
        show=show,
        access=access,
        host_available=show_data.status == "full",
        offset=0,
        result_places=result_places,
        qualifier_reveal=qualifier_reveal,
        show_name=show_data.name,
        short_name=show_data.short_name,
        show_id=show_data.id,
        year=year,
        year_id=_year,
        participants=participants,
        voters=voter_count,
        national_final_name=show_data.national_final_name,
        national_final_short_name=show_data.national_final_short_name,
    )


@bp.get("/<int:year>/<show>/detailed")
@with_auth
def detailed_results(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return render_template(
            "error.html", error="You aren't allowed to access the detailed results yet"
        ), 400

    if show_data.voting_closes and show_data.voting_closes > dt_now() and not elevated:
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    songs = get_show_result_entries(_year, show)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    voters, scores = _get_detailed_votes(show_data.id)

    songs.sort(reverse=True)

    qualifiers = show_data.primary_qualifiers
    sc_qualifiers = show_data.total_qualifiers

    return render_template(
        "year/detailed.html",
        qualifiers=qualifiers,
        sc_qualifiers=sc_qualifiers,
        songs=songs,
        voters=voters,
        scores=scores,
        show_name=show_data.name,
        show=show,
        year=year,
        participants=len(songs),
        national_final_name=show_data.national_final_name,
        national_final_short_name=show_data.national_final_short_name,
    )
