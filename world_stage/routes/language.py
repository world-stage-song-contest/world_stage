from flask import Blueprint, request

from ..db import get_db
from ..utils import (
    get_closed_years,
    get_language_history,
    get_show_results_for_songs,
    render_template,
)

bp = Blueprint("language", __name__, url_prefix="/language")


def _find_language(name: str) -> dict | None:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, name, tag, extlang, region, subvariant
        FROM language
        WHERE LOWER(name) = LOWER(%s)
        ORDER BY id
        LIMIT 1
        """,
        (name,),
    )
    return cursor.fetchone()


@bp.get("")
def index():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT language.id, language.name, language.tag, language.extlang,
               language.region, language.subvariant,
               COUNT(DISTINCT song.id) AS entry_count
        FROM language
        JOIN language_set_language AS member
          ON member.language_id = language.id
        JOIN current_song AS song
          ON song.language_set_id = member.language_set_id
        JOIN year ON year.id = song.year_id
                 AND year.status IN ('closed', 'ongoing')
        GROUP BY language.id
        ORDER BY LOWER(language.name), language.id
        """
    )
    return render_template("language/index.html", languages=cursor.fetchall())


@bp.get("/<path:name>")
def details(name: str):
    language = _find_language(name)
    if not language:
        return render_template("error.html", error="Language not found"), 404
    entries = get_language_history(language["id"])
    results = get_show_results_for_songs([entry.id for entry in entries])
    return render_template(
        "language/details.html",
        language=language,
        entries=[entry for entry in entries if entry.year.id >= 0 and entry.main_participant],
        special_entries=[
            entry for entry in entries if entry.year.id < 0 and entry.main_participant
        ],
        national_final_entries=[entry for entry in entries if entry.national_final_id],
        results=results,
    )


@bp.get("/<path:name>/bias")
def bias(name: str):
    language = _find_language(name)
    if not language:
        return render_template("error.html", error="Language not found"), 404

    year_from = request.args.get("from", type=int)
    year_to = request.args.get("to", type=int)
    include_revotes = request.args.get("include_revotes") == "true"
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT * FROM language_voter_bias(%s, %s, %s, %s)",
        (language["id"], year_from, year_to, include_revotes),
    )
    return render_template(
        "inbound_bias.html",
        subject_type="language",
        subject_name=language["name"],
        biases=[dict(row) for row in cursor],
        closed_years=get_closed_years(),
        year_from=year_from,
        year_to=year_to,
        include_specials=True,
        include_revotes=include_revotes,
    )
