import math
from collections import defaultdict

import psycopg
from flask import request

from ...db import get_db
from ...utils import (
    get_show_id,
    get_show_songs,
    get_year_shows,
    render_template,
)
from .common import _resolve_special, bp


def _render_draw(year_id: int, label: str):
    """Render the multi-show (semifinal) draw page for a given year. ``label``
    is shown in error messages and used as the JS RNG seed; for regular years
    that's just the numeric year, for specials it's the negative year id.

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
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT JOIN song_language sl
               ON sl.song_id = song.id AND sl.priority = 0
        WHERE song.year_id = %s AND NOT song.is_placeholder
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
        return render_template(
            "error.html", error=f"No semifinal shows found for {label}"
        ), 404
    per = semifinalists // count
    songs = [per] * count
    deficit = semifinalists - per * count
    # The `deficit` extra songs make some semis longer than others. Give
    # the extra to even-numbered semis first (2, 4, ...) so odd-numbered
    # semis (1, 3, ...) have priority to be the short ones. Shows are
    # 0-indexed here, so even-numbered semis are the odd indices.
    long_order = (
        [i for i in range(count) if i % 2 == 1]
        + [i for i in range(count) if i % 2 == 0]
    )
    for i in long_order[:deficit]:
        songs[i] += 1

    limits = list(map(lambda n: math.ceil(n / 2), songs))

    return render_template(
        "admin/draw.html",
        pots=pots,
        shows=shows,
        songs=songs,
        limits=limits,
        year=year_id,
        single_pot=single_pot,
    )


@bp.get("/draw/<int:year>")
def draw(year: int):
    return _render_draw(year, str(year))


@bp.get("/draw/special/<short_name>")
def draw_special(short_name: str):
    special = _resolve_special(short_name)
    if not special:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return _render_draw(special["id"], special["special_name"] or short_name)


@bp.post("/draw/<int:year>")
def draw_post(year: int):
    # Each value is a list of song IDs in running order.
    data: dict[str, list[int]] | None = request.json
    if not data:
        return {"error": "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    try:
        for show, ro in data.items():
            show_data = get_show_id(show, year)
            if not show_data:
                return {"error": f"Invalid show {show} for {year}"}, 400

            for i, song_id in enumerate(ro):
                # Verify the song actually belongs to this year before
                # attaching it to a show — guards against bad client input.
                cursor.execute(
                    "SELECT id FROM song WHERE id = %s AND year_id = %s",
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


@bp.post("/draw/special/<short_name>")
def draw_special_post(short_name: str):
    special = _resolve_special(short_name)
    if not special:
        return {"error": f"Special '{short_name}' not found"}, 404
    return draw_post(special["id"])


@bp.get("/draw/special/<short_name>/<show>")
def draw_special_final(short_name: str, show: str):
    special = _resolve_special(short_name)
    if not special:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return draw_final(special["id"], show)


@bp.post("/draw/special/<short_name>/<show>")
def draw_special_final_post(short_name: str, show: str):
    special = _resolve_special(short_name)
    if not special:
        return {"error": f"Special '{short_name}' not found"}, 404
    return draw_final_post(special["id"], show)


@bp.get("/draw/<int:year>/<show>")
def draw_final(year: int, show: str):
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error=f"Invalid show '{show}' for {year}"), 404

    songs = get_show_songs(year, show, sort_reveal=True)

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
        SELECT song_id, language_id FROM song_language
        WHERE song_id = ANY(%s) AND priority = 0
        """,
        ([s.id for s in songs],),
    )
    language_by_song = {row["song_id"]: row["language_id"] for row in cursor.fetchall()}

    return render_template(
        "admin/draw_individual.html",
        songs=songs,
        genre_by_cc=genre_by_cc,
        language_by_song=language_by_song,
        show=show,
        show_name=show_data.name,
        year=year,
        num=len(songs),
        lim=math.ceil((len(songs) / 2) or 1),
        single_pot=year < 0,
    )


@bp.post("/draw/<int:year>/<show>")
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
            "SELECT id FROM song WHERE id = %s AND year_id = %s",
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
