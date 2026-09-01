import math
from collections import defaultdict

import psycopg
from flask import request, url_for

from ...db import get_db
from ...utils import (
    draw_running_order,
    draw_semifinals,
    get_show_draw_entries,
    get_show_id,
    get_show_lineup,
    get_year_shows,
    render_template,
)
from .common import _resolve_special, bp


def _render_draw(year_id: int, label: str, manage_url: str):
    """Render the multi-show (semifinal) draw page for a given year. ``label``
    is shown in error messages and navigation; for regular years that's just
    the numeric year, while specials use their display name.

    Regular years group entries by ``country.pot`` (entries without a pot
    are excluded — same as the original behavior). Specials collapse every
    entry into a single combined pot.
    """
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT song.id AS song_id, song.title, song.entry_number,
               song.submitter_id AS submitter,
               country.id AS cc, country.name, country.pot, country.genre,
               sl.language_id AS language
        FROM current_song AS song
        JOIN country ON song.country_id = country.id
        LEFT JOIN language_set_language sl
               ON sl.language_set_id = song.language_set_id AND sl.priority = 0
        WHERE song.year_id = %s AND song.main_participant AND NOT song.is_placeholder
        ORDER BY country.name, song.entry_number
        """,
        (year_id,),
    )
    entries = cursor.fetchall()

    single_pot = year_id < 0
    if single_pot:
        pots: dict[int, list[dict]] = {1: list(entries)}
    else:
        pots_raw: dict[int, list[dict]] = defaultdict(list)
        for entry in entries:
            pot = entry.get("pot")
            if pot is not None:
                pots_raw[pot].append(entry)
        pots = {k: pots_raw[k] for k in sorted(pots_raw.keys())}

    semifinalists = sum(len(p) for p in pots.values())

    shows = get_year_shows(year_id, pattern="sf")
    count = len(shows)
    if count == 0:
        return render_template("error.html", error=f"No semifinal shows found for {label}"), 404
    per = semifinalists // count
    songs = [per] * count
    deficit = semifinalists - per * count
    # The `deficit` extra songs make some semis longer than others. Give
    # the extra to even-numbered semis first (2, 4, ...) so odd-numbered
    # semis (1, 3, ...) have priority to be the short ones. Shows are
    # 0-indexed here, so even-numbered semis are the odd indices.
    long_order = [i for i in range(count) if i % 2 == 1] + [i for i in range(count) if i % 2 == 0]
    for i in long_order[:deficit]:
        songs[i] += 1

    limits = list(map(lambda n: math.ceil(n / 2), songs))
    show_names = [show["short_name"] for show in shows]
    try:
        draw_assignments = draw_semifinals(
            pots,
            show_names,
            songs,
            year_id,
            single_pot=single_pot,
        )
    except ValueError as e:
        return render_template("error.html", error=str(e)), 400

    entry_show_by_song = {
        entry["song_id"]: show
        for show, assigned_entries in draw_assignments.items()
        for entry in assigned_entries
    }

    return render_template(
        "admin/draw.html",
        pots=pots,
        shows=shows,
        songs=songs,
        limits=limits,
        draw_assignments=draw_assignments,
        entry_show_by_song=entry_show_by_song,
        year=year_id,
        year_label=label,
        manage_url=manage_url,
        single_pot=single_pot,
    )


@bp.get("/manage/<int:year>/draw")
def draw(year: int):
    return _render_draw(year, str(year), url_for("admin.manage", year=year))


@bp.get("/manage/special/<short_name>/draw")
def draw_special(short_name: str):
    special = _resolve_special(short_name)
    if not special:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return _render_draw(
        special["id"],
        special["special_name"] or short_name,
        url_for("admin.manage_special", short_name=short_name),
    )


def _validate_regular_draw_pots(cursor, year: int, data: dict[str, list[int]]) -> str | None:
    if year < 0:
        return None

    show_count = len(get_year_shows(year, pattern="sf"))
    if show_count == 0:
        return f"No semifinal shows found for {year}"
    cursor.execute(
        """
        SELECT country.pot, COUNT(*) AS entries
        FROM current_song AS song
        JOIN country ON song.country_id = country.id
        WHERE song.year_id = %s AND song.main_participant
          AND NOT song.is_placeholder
          AND country.pot IS NOT NULL
        GROUP BY country.pot
        """,
        (year,),
    )
    pot_limits = {row["pot"]: math.ceil(row["entries"] / show_count) for row in cursor.fetchall()}

    for show, ro in data.items():
        if not ro:
            continue

        cursor.execute(
            """
            SELECT song.id AS song_id, country.pot
            FROM current_song AS song
            JOIN country ON song.country_id = country.id
            WHERE song.year_id = %s AND song.main_participant AND song.id = ANY(%s)
            """,
            (year, ro),
        )
        pot_by_song = {row["song_id"]: row["pot"] for row in cursor.fetchall()}
        missing = [song_id for song_id in ro if song_id not in pot_by_song]
        if missing:
            return f"Song {missing[0]} not found in year {year}"

        seen: dict[int, int] = defaultdict(int)
        for song_id in ro:
            pot = pot_by_song[song_id]
            if pot is None:
                return f"Song {song_id} has no pot"
            seen[pot] += 1
            if seen[pot] > pot_limits.get(pot, 0):
                return f"Show {show} contains multiple entries from pot {pot}"

    return None


@bp.post("/manage/<int:year>/draw")
def draw_post(year: int):
    # Each value is a list of song IDs in running order.
    data: dict[str, list[int]] | None = request.json
    if not data:
        return {"error": "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    validation_error = _validate_regular_draw_pots(cursor, year, data)
    if validation_error:
        return {"error": validation_error}, 400

    try:
        for show, ro in data.items():
            show_data = get_show_id(show, year)
            if not show_data:
                return {"error": f"Invalid show {show} for {year}"}, 400

            for i, song_id in enumerate(ro):
                # Verify the song actually belongs to this year before
                # attaching it to a show — guards against bad client input.
                cursor.execute(
                    "SELECT id FROM current_song AS song WHERE id = %s AND year_id = %s",
                    (song_id, year),
                )
                if not cursor.fetchone():
                    return {"error": f"Song {song_id} not found in year {year}"}, 400

                cursor.execute(
                    """
                    INSERT INTO song_show (song_id, show_id, running_order)
                    VALUES (%s, %s, %s)
                """,
                    (song_id, show_data.id, i + 1),
                )
    except psycopg.IntegrityError as e:
        print(e)
        return {"error": "Duplicate data"}, 400

    db.commit()
    return {}, 204


@bp.post("/manage/special/<short_name>/draw")
def draw_special_post(short_name: str):
    special = _resolve_special(short_name)
    if not special:
        return {"error": f"Special '{short_name}' not found"}, 404
    return draw_post(special["id"])


@bp.get("/manage/special/<short_name>/draw/<show>")
def draw_special_final(short_name: str, show: str):
    special = _resolve_special(short_name)
    if not special:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return draw_final(
        special["id"],
        show,
        manage_url=url_for("admin.manage_special", short_name=short_name),
        year_label=special["special_name"] or short_name,
    )


@bp.post("/manage/special/<short_name>/draw/<show>")
def draw_special_final_post(short_name: str, show: str):
    special = _resolve_special(short_name)
    if not special:
        return {"error": f"Special '{short_name}' not found"}, 404
    return draw_final_post(special["id"], show)


@bp.get("/manage/<int:year>/draw/<show>")
def draw_final(
    year: int,
    show: str,
    *,
    manage_url: str | None = None,
    year_label: str | None = None,
):
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error=f"Invalid show '{show}' for {year}"), 404

    songs = (
        get_show_draw_entries(year, show)
        if show_data.status == "none"
        else get_show_lineup(year, show)
    )

    if not songs:
        return render_template("error.html", error="No show '{show}' found for {year}"), 404

    # Genre is set per-country (not per-song) and isn't on the Country
    # dataclass, so look it up separately and pass it as a {cc → genre}
    # mapping to the template. Same idea for the per-song primary
    # language ({song_id → language_id}).
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, genre FROM country WHERE id = ANY(%s)",
        ([s.country.cc for s in songs],),
    )
    genre_by_cc = {row["id"]: row["genre"] for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT song.id AS song_id, member.language_id
        FROM current_song AS song
        JOIN language_set_language AS member
          ON member.language_set_id = song.language_set_id
        WHERE song.id = ANY(%s) AND member.priority = 0
        """,
        ([s.id for s in songs],),
    )
    language_by_song = {row["song_id"]: row["language_id"] for row in cursor.fetchall()}
    if show_data.status == "none":
        song_by_id = {song.id: song for song in songs}
        draw_order_entries = draw_running_order(
            [
                {
                    "song_id": song.id,
                    "cc": song.country.cc,
                    "submitter": song.submitter_id,
                    "genre": genre_by_cc.get(song.country.cc),
                    "language": language_by_song.get(song.id),
                }
                for song in songs
            ],
            f"{year}:{show}",
        )
        draw_order = [song_by_id[entry["song_id"]] for entry in draw_order_entries]
    else:
        draw_order = songs

    return render_template(
        "admin/draw_individual.html",
        songs=songs,
        draw_order=draw_order,
        genre_by_cc=genre_by_cc,
        language_by_song=language_by_song,
        show=show,
        show_name=show_data.name,
        year=year,
        year_label=year_label or str(year),
        manage_url=manage_url or url_for("admin.manage", year=year),
        num=len(songs),
        lim=math.ceil((len(songs) / 2) or 1),
        single_pot=year < 0,
    )


@bp.post("/manage/<int:year>/draw/<show>")
def draw_final_post(year: int, show: str):
    # The client now sends a list of song IDs (in running order).
    data: dict[str, list[int]] | None = request.json
    if not data:
        return {"error": "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    show_data = get_show_id(show, year)
    if not show_data:
        return {"error": f"Invalid show '{show}' for {year}"}, 400

    ro = data.get(show)
    if ro is None:
        return {"error": "No running order provided"}, 400

    for i, song_id in enumerate(ro):
        cursor.execute(
            "SELECT id FROM current_song AS song WHERE id = %s AND year_id = %s",
            (song_id, year),
        )
        if not cursor.fetchone():
            return {"error": f"Song {song_id} not found in year {year}"}, 400

        cursor.execute(
            """
            UPDATE song_show
            SET running_order = %s
            WHERE song_id = %s AND show_id = %s
        """,
            (i + 1, song_id, show_data.id),
        )

    db.commit()
    return {}, 204
