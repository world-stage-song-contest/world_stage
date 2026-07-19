import typing

from flask import request

from ...db import get_db
from ...utils import (
    LCG,
    UserPermissions,
    dt_now,
    get_show_id,
    get_votes_for_songs,
    render_template,
    with_permissions,
)
from .common import bp, resolve_special


@bp.get("/<int:year>/<show>/qualifiers")
@with_permissions
def qualifiers(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if show_data.dtf is None:
        return render_template("error.html", error="Not a semi-final."), 400

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
@with_permissions
def qualifiers_post(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400

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
@with_permissions
def qualifiers_scores(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400

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
    rows = cursor.fetchall()
    votes_by_song = get_votes_for_songs(
        {row["id"]: row["running_order"] for row in rows}, show_data.id
    )
    countries = []
    for row in rows:
        val = {
            "id": row["id"],
            "title": row["title"],
            "country": row["country"],
            "cc": row["cc"],
            "entry_number": row["entry_number"],
            "points": votes_by_song[row["id"]],
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
@with_permissions
def special_qualifiers(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if show_data.dtf is None:
        return render_template("error.html", error="Not a semi-final."), 400

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
