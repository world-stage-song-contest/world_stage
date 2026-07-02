import contextlib
import datetime
import json

import psycopg
from flask import redirect, request, url_for

from ...db import get_db
from ...utils import (
    get_years,
    render_template,
)
from .common import _resolve_special, bp


@bp.get("/manage/<int:year>/createshow")
def create_show(year: int):
    return render_template("admin/create_show.html", years=get_years(), year=year)


@bp.post("/manage/<int:year>/createshow")
def create_show_post(year: int):
    data: dict[str, int | str | None] = {"year": year}
    value: int | str | None
    for key, value_ in request.form.items():
        value = value_
        with contextlib.suppress(ValueError):
            value = int(value)

        if not value:
            value = None

        data[key] = value

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
        """
        INSERT INTO show (year_id, point_system_id, show_name, short_name, dtf, sc, date, status)
        VALUES (%(year)s, 1, %(show_name)s, %(short_name)s, %(dtf)s, %(sc)s, %(date)s, 'none')
        """,
            data,
        )
        db.commit()
    except psycopg.Error as e:
        return render_template("admin/create_show.html", error=str(e))

    return redirect(url_for("admin.create_show", year=year))

@bp.get("/manage")
def manage_index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT id, status, special_name, special_short_name
        FROM year ORDER BY id
        """
    )
    rows = cursor.fetchall()
    regular_years = [r for r in rows if r["id"] >= 0]
    specials = [r for r in rows if r["id"] < 0]

    return render_template(
        "admin/manage_index.html", years=regular_years, specials=specials
    )


def _render_manage(year_id: int, year_data: dict):
    """Render the show-management page for a given year row."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show_name, short_name, date, status, voting_opens, voting_closes, predictions_close
        FROM show WHERE year_id = %s
        ORDER BY id
    """,
        (year_id,),
    )
    shows = cursor.fetchall()
    return render_template("admin/manage_shows.html", year=year_data, shows=shows)


@bp.get("/manage/<int:year>")
def manage(year: int):
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, status FROM year WHERE id = %s AND id >= 0",
        (year,),
    )
    year_data = cursor.fetchone()
    if not year_data:
        return render_template("error.html", error=f"Year {year} not found"), 404

    return _render_manage(year, year_data)


@bp.get("/manage/special/<short_name>")
def manage_special(short_name: str):
    year_data = _resolve_special(short_name)
    if not year_data:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404

    return _render_manage(year_data["id"], year_data)


@bp.post("/manage/<int:year>")
def manage_post(year: int):
    body = request.get_json()
    if not body:
        return render_template("error.html", error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    action = body.get("action")
    if not action:
        return render_template("error.html", error="No action specified"), 400

    match action:
        case "change_year_status":
            status = body.get("year_status")
            if status not in ("open", "closed", "ongoing"):
                return render_template("error.html", error="Invalid year status"), 400

            cursor.execute(
                """
                UPDATE year
                SET status = %s
                WHERE id = %s
            """,
                (status, year),
            )
        case _:
            return render_template("error.html", error=f"Unknown action '{action}'"), 400
    db.commit()
    return {"status": "success"}, 200


@bp.post("/manage/special/<short_name>")
def manage_special_post(short_name: str):
    year = _resolve_special(short_name)
    if not year:
        return {"error": f"Special '{short_name}' not found"}, 404
    return manage_post(year["id"])


@bp.post("/manage/special/<short_name>/<show>")
def manage_show_special_post(short_name: str, show: str):
    year = _resolve_special(short_name)
    if not year:
        return {"error": f"Special '{short_name}' not found"}, 404
    return manage_show_post(year["id"], show)


@bp.post("/manage/<int:year>/<show>")
def manage_show_post(year: int, show: str):
    body = request.get_json()
    if not body:
        return render_template("error.html", error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    action = body.get("action")
    if not action:
        return render_template("error.html", error="No action specified"), 400

    match action:
        case "open_voting":
            cursor.execute(
                """
                UPDATE show
                SET voting_opens = COALESCE(voting_opens, CURRENT_TIMESTAMP)
                  , voting_closes = NULL
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "close_voting":
            cursor.execute(
                """
                UPDATE show
                SET voting_closes = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "close_predictions":
            cursor.execute(
                """
                UPDATE show
                SET predictions_close = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "open_predictions":
            cursor.execute(
                """
                UPDATE show
                SET predictions_close = NULL
                WHERE year_id = %s AND short_name = %s
            """,
                (year, show),
            )
        case "set_status":
            status = body.get("status")
            if status not in ("none", "draw", "partial", "full"):
                return render_template("error.html", error="Invalid show status"), 400

            cursor.execute(
                """
                UPDATE show
                SET status = %s
                WHERE year_id = %s AND short_name = %s
            """,
                (status, year, show),
            )
        case "change_date":
            date_str = body.get("date")
            if not date_str:
                return render_template("error.html", error="No date provided"), 400
            try:
                date = datetime.date.fromisoformat(date_str)
            except ValueError:
                return render_template("error.html", error="Invalid date format"), 400
            if not date:
                return render_template("error.html", error="Invalid date format"), 400

            cursor.execute(
                """
                UPDATE show
                SET date = %s
                WHERE year_id = %s AND short_name = %s
            """,
                (date, year, show),
            )
        case _:
            return render_template("error.html", error=f"Unknown action '{action}'"), 400

    db.commit()

    return {"status": "success"}, 200

@bp.get("/manage/<int:year>/setpots")
def set_pots(year: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT country.id, name, pot, genre FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON song.year_id = year.id
        WHERE year_id = %s AND year.host_id IS DISTINCT FROM country.id
        ORDER BY pot, name
    """,
        (year,),
    )
    countries = cursor.fetchall()

    return render_template("admin/set_pots.html", countries=countries, year=year)


@bp.post("/manage/<int:year>/setpots")
def set_pots_post(year: int):
    db = get_db()
    cursor = db.cursor()

    # Form fields are name-prefixed: ``pot_<country_id>`` and
    # ``genre_<country_id>``. Both follow the same "0 → NULL" convention.
    updates: dict[str, dict[str, int | None]] = {}
    for key, value in request.form.items():
        if key.startswith("pot_"):
            field, country_id = "pot", key[len("pot_"):]
        elif key.startswith("genre_"):
            field, country_id = "genre", key[len("genre_"):]
        else:
            continue
        try:
            parsed: int | None = int(value)
            if parsed == 0:
                parsed = None
        except ValueError:
            return render_template(
                "error.html",
                error=f"Invalid {field} value for country {country_id}",
            ), 400
        updates.setdefault(country_id, {})[field] = parsed

    # Clear all pots/genres first so countries no longer in the form
    # (e.g. removed from this year) are reset.
    cursor.execute("UPDATE country SET pot = NULL, genre = NULL")

    for country_id, fields in updates.items():
        cursor.execute(
            """
            UPDATE country
            SET pot = %s, genre = %s
            WHERE id = %s
        """,
            (fields.get("pot"), fields.get("genre"), country_id),
        )

    db.commit()
    return redirect(url_for("admin.set_pots", year=year))


@bp.post("/manage/<int:year>/setpots/json")
def set_pots_json(year: int):
    """Bulk-update pots/genres from a single JSON payload of the form
    ``{"US": {"pot": 1, "genre": 1}, "RU": {"pot": 2, "genre": 3}, ...}``.

    Keys may be either a country code (the ``id``) or a full country
    name; both are matched case-insensitively.

    Same conventions as the form-encoded endpoint: a value of 0 (or a
    missing key) maps to NULL, and any country not listed in the payload
    has its pot and genre cleared.
    """
    # The payload may arrive as raw JSON in the request body or as a
    # ``payload`` form field (used by the textarea on the page).
    raw_payload: dict | None = None
    if request.is_json:
        raw_payload = request.get_json(silent=True)
    else:
        text = request.form.get("payload", "").strip()
        if text:
            try:
                raw_payload = json.loads(text)
            except json.JSONDecodeError as e:
                return render_template("error.html", error=f"Invalid JSON: {e}"), 400

    if raw_payload is None:
        return render_template("error.html", error="No JSON payload provided"), 400
    if not isinstance(raw_payload, dict):
        return render_template(
            "error.html", error="Top-level JSON value must be an object"
        ), 400

    def _coerce(label: str, country_id: str, value):
        """Apply the same '0 / null → NULL' convention as the form path."""
        if value is None:
            return None
        try:
            n = int(value)
        except (ValueError, TypeError) as err:
            raise ValueError(f"Invalid {label} for country {country_id}: {value!r}") from err
        return None if n == 0 else n

    db = get_db()
    cursor = db.cursor()

    # Build a case-insensitive lookup so payload keys may be either a
    # country code (the ``id``) or a full country name.
    cursor.execute("SELECT id, name FROM country")
    by_code: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for row in cursor.fetchall():
        by_code[row["id"].casefold()] = row["id"]
        if row["name"]:
            by_name[row["name"].casefold()] = row["id"]

    def _resolve(key: str) -> str | None:
        k = key.strip().casefold()
        return by_code.get(k) or by_name.get(k)

    # Validate everything before touching the database so a bad payload
    # doesn't half-apply.
    parsed: dict[str, tuple[int | None, int | None]] = {}
    for key, fields in raw_payload.items():
        # Ignore an empty-string key (e.g. a trailing blank entry).
        if not key.strip():
            continue
        country_id = _resolve(key)
        if country_id is None:
            return render_template(
                "error.html", error=f"Unknown country: {key!r}"
            ), 400
        if not isinstance(fields, dict):
            return render_template(
                "error.html",
                error=f"Expected object for country {key}",
            ), 400
        try:
            pot = _coerce("pot", key, fields.get("pot"))
            genre = _coerce("genre", key, fields.get("genre"))
        except ValueError as e:
            return render_template("error.html", error=str(e)), 400
        parsed[country_id] = (pot, genre)

    cursor.execute("UPDATE country SET pot = NULL, genre = NULL")
    for country_id, (pot, genre) in parsed.items():
        cursor.execute(
            "UPDATE country SET pot = %s, genre = %s WHERE id = %s",
            (pot, genre, country_id),
        )

    db.commit()
    return redirect(url_for("admin.set_pots", year=year))
