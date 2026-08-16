import psycopg
from flask import redirect, request

from ...db import get_db
from ...utils import (
    LCG,
    UserPermissions,
    can_manage_show,
    dt_now,
    get_show_id,
    get_votes_for_songs,
    render_template,
    with_auth,
)
from ...utils.artists import fetch_song_artist_credits
from .common import bp, resolve_special
from .themes import qualifier_theme


def _manual_special_qualifiers(
    year_id: int,
    show: str,
    user,
    permissions: UserPermissions,
    *,
    year_label: str,
    special: str | None = None,
):
    show_data = get_show_id(show, year_id)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    if not show_data.progressions:
        return render_template("error.html", error="This show has no progression."), 400
    if not can_manage_show(show_data, user, permissions):
        return render_template("error.html", error="You aren't allowed to manage this show"), 403

    db = get_db()
    cursor = db.cursor()
    if request.method == "POST":
        try:
            action = request.form.get("action")
            song_id = int(request.form.get("song_id", ""))
            if action == "add":
                target_id = int(request.form.get("target_show_id", ""))
                if target_id not in {
                    progression["target_show_id"]
                    for progression in show_data.progressions
                }:
                    raise ValueError("Invalid progression destination")
                cursor.execute(
                    "SELECT 1 FROM song_show WHERE show_id = %s AND song_id = %s",
                    (show_data.id, song_id),
                )
                if not cursor.fetchone():
                    raise ValueError("The song is not in this show")
                cursor.execute(
                    "SELECT 1 FROM show_qualifier "
                    "WHERE source_show_id = %s AND song_id = %s",
                    (show_data.id, song_id),
                )
                if cursor.fetchone():
                    raise ValueError("The song has already qualified")
                cursor.execute(
                    """
                    SELECT entry_status
                    FROM country_show_results
                    WHERE show_id = %s AND song_id = %s
                      AND result_mode = 'official'
                    """,
                    (show_data.id, song_id),
                )
                result = cursor.fetchone()
                if not result or result["entry_status"] != "nq":
                    raise ValueError(
                        "Only a non-qualifier can be advanced under special circumstances"
                    )
                cursor.execute(
                    "SELECT COALESCE(MAX(running_order), 0) AS last_order "
                    "FROM song_show WHERE show_id = %s",
                    (target_id,),
                )
                running_order = cursor.fetchone()["last_order"] + 1
                cursor.execute(
                    "INSERT INTO song_show (song_id, show_id, running_order) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (song_id, target_id, running_order),
                )
                cursor.execute(
                    "SELECT COALESCE(MAX(qualifier_order), 0) AS last_order "
                    "FROM show_qualifier "
                    "WHERE source_show_id = %s AND target_show_id = %s",
                    (show_data.id, target_id),
                )
                qualifier_order = cursor.fetchone()["last_order"] + 1
                cursor.execute(
                    """
                    INSERT INTO show_qualifier (
                        source_show_id, target_show_id, song_id,
                        qualifier_order, is_special
                    ) VALUES (%s, %s, %s, %s, true)
                    """,
                    (show_data.id, target_id, song_id, qualifier_order),
                )
            elif action == "remove":
                cursor.execute(
                    """
                    DELETE FROM show_qualifier
                    WHERE source_show_id = %s AND song_id = %s AND is_special
                    RETURNING target_show_id
                    """,
                    (show_data.id, song_id),
                )
                removed = cursor.fetchone()
                if not removed:
                    raise ValueError("Special qualifier not found")
                cursor.execute(
                    "DELETE FROM song_show WHERE show_id = %s AND song_id = %s",
                    (removed["target_show_id"], song_id),
                )
            else:
                raise ValueError("Invalid action")

            cursor.execute(
                "SELECT refresh_show_results_for_mode(%s, 'official')",
                (show_data.id,),
            )
            db.commit()
            return redirect(request.path)
        except (psycopg.Error, ValueError) as exc:
            db.rollback()
            return render_template("error.html", error=str(exc)), 400

    cursor.execute(
        """
        SELECT song.id, song.country_id, country.name AS country,
               song.artist, song.title
        FROM song_show AS source_entry
        JOIN current_song AS song ON song.id = source_entry.song_id
        JOIN country ON country.id = song.country_id
        WHERE source_entry.show_id = %s
          AND NOT EXISTS (
              SELECT 1 FROM show_qualifier
              WHERE source_show_id = %s AND song_id = song.id
          )
          AND EXISTS (
              SELECT 1 FROM country_show_results
              WHERE show_id = %s AND song_id = song.id
                AND result_mode = 'official' AND entry_status = 'nq'
          )
        ORDER BY country.name, song.artist, song.title
        """,
        (show_data.id, show_data.id, show_data.id),
    )
    candidates = cursor.fetchall()
    cursor.execute(
        """
        SELECT qualifier.song_id, song.country_id, country.name AS country,
               song.artist, song.title, target.show_name AS target_name
        FROM show_qualifier AS qualifier
        JOIN current_song AS song ON song.id = qualifier.song_id
        JOIN country ON country.id = song.country_id
        JOIN show AS target ON target.id = qualifier.target_show_id
        WHERE qualifier.source_show_id = %s AND qualifier.is_special
        ORDER BY qualifier.qualifier_order
        """,
        (show_data.id,),
    )
    special_qualifiers = cursor.fetchall()
    song_ids = [song["id"] for song in candidates] + [
        song["song_id"] for song in special_qualifiers
    ]
    artist_credits = fetch_song_artist_credits(cursor, song_ids)
    for song in candidates:
        song["artists"] = artist_credits.get(song["id"], [])
    for song in special_qualifiers:
        song["artists"] = artist_credits.get(song["song_id"], [])
    return render_template(
        "year/special_qualifiers.html",
        show=show,
        show_name=show_data.name,
        year=year_label,
        special=special,
        candidates=candidates,
        progressions=show_data.progressions,
        special_qualifiers=special_qualifiers,
    )


@bp.route("/<int:year>/<show>/qualifiers/special", methods=["GET", "POST"])
@with_auth
def manage_special_qualifiers(
    year: int, show: str, user, permissions: UserPermissions
):
    return _manual_special_qualifiers(
        year, show, user, permissions, year_label=str(year)
    )


@bp.route(
    "/special/<short_name>/<show>/qualifiers/special", methods=["GET", "POST"]
)
@with_auth
def manage_special_year_qualifiers(
    short_name: str, show: str, user, permissions: UserPermissions
):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404
    return _manual_special_qualifiers(
        special_year["id"],
        show,
        user,
        permissions,
        year_label=special_year["special_name"] or short_name,
        special=short_name,
    )


@bp.get("/<int:year>/<show>/qualifiers")
@with_auth
def qualifiers(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if not show_data.progressions:
        return render_template("error.html", error="This show has no progression."), 400

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return render_template("error.html", error="You aren't allowed to access the qualifiers")

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not elevated
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template(
        "year/qualifiers.html",
        show=show,
        year=year,
        show_name=show_data.name,
        progressions=show_data.progressions,
        **qualifier_theme(_year),
    )


@bp.post("/<int:year>/<show>/qualifiers")
@with_auth
def qualifiers_post(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if not show_data.progressions:
        return {"error": "This show has no progression."}, 400

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return {"error": "You aren't allowed to access the qualifiers"}, 400

    body = request.json
    if not body or not isinstance(body, dict):
        return {"error": "Invalid request body"}, 400

    action = body.get("action")
    if action != "save":
        return {"error": "Invalid action"}, 400

    db = get_db()
    cursor = db.cursor()

    progression_orders = body.get("progressions")
    if not isinstance(progression_orders, dict):
        return {"error": "Progression reveal order not provided"}, 400

    requested: list[tuple[int, int, int, bool]] = []
    seen_song_ids: set[int] = set()
    for progression in show_data.progressions:
        target_id = progression["target_show_id"]
        entries = progression_orders.get(str(target_id))
        if not isinstance(entries, list):
            return {"error": f"Invalid reveal order for {progression['target_name']}"}, 400
        normal_count = 0
        for order, entry in enumerate(entries, 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("song_id"), int):
                return {"error": "Invalid qualifier entry"}, 400
            song_id = entry["song_id"]
            is_special = entry.get("is_special") is True
            if song_id in seen_song_ids:
                return {"error": "A song can only qualify once from a show"}, 400
            seen_song_ids.add(song_id)
            normal_count += not is_special
            requested.append((target_id, song_id, order, is_special))
        if normal_count != progression["qualifier_count"]:
            return {
                "error": f"{progression['target_name']} needs "
                f"{progression['qualifier_count']} regular qualifiers"
            }, 400

    if seen_song_ids:
        cursor.execute(
            "SELECT song_id FROM song_show "
            "WHERE show_id = %s AND song_id = ANY(%s)",
            (show_data.id, list(seen_song_ids)),
        )
        if {row["song_id"] for row in cursor.fetchall()} != seen_song_ids:
            return {"error": "Every qualifier must participate in the source show"}, 400

    cursor.execute(
        "SELECT target_show_id, song_id FROM show_qualifier "
        "WHERE source_show_id = %s",
        (show_data.id,),
    )
    previous = {(row["target_show_id"], row["song_id"]) for row in cursor.fetchall()}
    desired = {(target_id, song_id) for target_id, song_id, _order, _special in requested}
    cursor.execute(
        "DELETE FROM show_qualifier WHERE source_show_id = %s",
        (show_data.id,),
    )

    for target_id, song_id, order, is_special in requested:
        cursor.execute(
            "SELECT COALESCE(MAX(running_order), 0) AS last_order "
            "FROM song_show WHERE show_id = %s",
            (target_id,),
        )
        next_order = cursor.fetchone()["last_order"]
        cursor.execute(
            """
            INSERT INTO song_show (song_id, show_id, running_order)
            VALUES (%s, %s, %s)
            ON CONFLICT (show_id, song_id) DO NOTHING
            """,
            (song_id, target_id, next_order + 1),
        )
        cursor.execute(
            """
            INSERT INTO show_qualifier (
                source_show_id, target_show_id, song_id,
                qualifier_order, is_special
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (show_data.id, target_id, song_id, order, is_special),
        )

    for target_id, song_id in previous - desired:
        cursor.execute(
            "DELETE FROM song_show WHERE show_id = %s AND song_id = %s",
            (target_id, song_id),
        )

    cursor.execute(
        "SELECT refresh_show_results_for_mode(%s, 'official')",
        (show_data.id,),
    )
    db.commit()

    return {"success": True, "message": "Qualifiers saved successfully."}


@bp.get("/<int:year>/<show>/qualifiers/votes")
@with_auth
def qualifiers_scores(year: int, show: str, user, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if not show_data.progressions:
        return {"error": "This show has no progression."}, 400

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return {"error": "You aren't allowed to access the qualifiers"}, 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not elevated
    ):
        return {"error": "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id, song.title, song.entry_number, song_show.running_order,
               country.name AS country, country.id AS cc
        FROM current_song AS song
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
            "running_order": row["running_order"],
            "points": votes_by_song[row["id"]],
        }
        countries.append(val)

    countries.sort(key=lambda x: x["points"], reverse=True)

    lcg = LCG(show_data.id)
    reveal_order: dict[str, list[dict]] = {}
    offset = 0
    for progression in show_data.progressions:
        count = progression["qualifier_count"]
        selected = [dict(entry, is_special=False) for entry in countries[offset : offset + count]]
        lcg.shuffle(selected)
        reveal_order[str(progression["target_show_id"])] = selected
        offset += count

    cursor.execute(
        """
        SELECT qualifier.target_show_id, qualifier.song_id,
               qualifier.qualifier_order, qualifier.is_special
        FROM show_qualifier AS qualifier
        WHERE qualifier.source_show_id = %s
        ORDER BY qualifier.target_show_id, qualifier.qualifier_order
        """,
        (show_data.id,),
    )
    saved_qualifiers = cursor.fetchall()
    if saved_qualifiers:
        countries_by_id = {country["id"]: country for country in countries}
        saved_by_target: dict[str, list[dict]] = {}
        for qualifier in saved_qualifiers:
            country = countries_by_id.get(qualifier["song_id"])
            if country:
                saved_by_target.setdefault(str(qualifier["target_show_id"]), []).append(
                    dict(country, is_special=qualifier["is_special"])
                )
        reveal_order.update(saved_by_target)

    countries.sort(key=lambda x: x["points"].ro)
    for country in countries:
        del country["points"]

    return {
        "countries": countries,
        "reveal_order": reveal_order,
        "progressions": show_data.progressions,
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
@with_auth
def special_qualifiers(short_name: str, show: str, user, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if not show_data.progressions:
        return render_template("error.html", error="This show has no progression."), 400

    elevated = can_manage_show(show_data, user, permissions)
    if show_data.status != "full" and not elevated:
        return render_template("error.html", error="You aren't allowed to access the qualifiers")

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not elevated
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    return render_template(
        "year/qualifiers.html",
        show=show,
        year=short_name,
        show_name=show_data.name,
        special=short_name,
        special_name=special_year["special_name"],
        progressions=show_data.progressions,
        **qualifier_theme(_year),
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
