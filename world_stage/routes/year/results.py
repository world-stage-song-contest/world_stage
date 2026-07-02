

from ...db import fetchone, get_db
from ...utils import (
    LCG,
    UserPermissions,
    dt_now,
    get_show_id,
    get_show_songs,
    render_template,
    with_permissions,
)
from .common import bp, get_other_shows, resolve_special


@bp.get("/special/<short_name>/<show>")
@with_permissions
def special_results(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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
@with_permissions
def special_detailed_results(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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

@bp.get("/<int:year>/<show>")
@with_permissions
def results(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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
@with_permissions
def detailed_results(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

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
