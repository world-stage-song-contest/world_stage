import datetime
import json
import re

import psycopg
from flask import redirect, request, url_for

from ...db import get_db
from ...utils import (
    get_lineup_issues,
    get_unassigned_lineup_issue,
    get_years,
    render_template,
)
from .common import _resolve_special, bp

NF_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _show_creation_context(year: int, **extra):
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT special_name, special_short_name FROM year WHERE id = %s", (year,)
    )
    year_data = cursor.fetchone() or {}
    cursor.execute(
        """
        SELECT point_system.id,
               array_agg(point.score ORDER BY point.place)
                   FILTER (WHERE point.id IS NOT NULL) AS points
        FROM point_system
        LEFT JOIN point ON point.point_system_id = point_system.id
        GROUP BY point_system.id
        ORDER BY point_system.id
        """
    )
    point_systems = cursor.fetchall()
    cursor.execute(
        """
        SELECT id, name FROM show_types ORDER BY sort_order
        """
    )
    show_types = cursor.fetchall()
    cursor.execute(
        """
        SELECT national_final.id, national_final.short_name, national_final.name,
               account.username AS owner, national_final.owner_country_id,
               COALESCE(MAX(show.show_number)
                   FILTER (WHERE show.show_type = 'sf'), 0) + 1
                   AS next_semifinal_number
        FROM national_final
        JOIN account ON account.id = national_final.owner_id
        LEFT JOIN show ON show.national_final_id = national_final.id
        WHERE national_final.year_id = %s
          AND national_final.status <> 'cancelled'
        GROUP BY national_final.id, account.username
        ORDER BY national_final.name
        """,
        (year,),
    )
    national_finals = cursor.fetchall()
    cursor.execute(
        """
        SELECT COALESCE(MAX(show_number), 0) + 1 AS next_semifinal_number
        FROM show
        WHERE year_id = %s AND national_final_id IS NULL AND show_type = 'sf'
        """,
        (year,),
    )
    main_next_semifinal_number = cursor.fetchone()["next_semifinal_number"]
    cursor.execute(
        """
        SELECT show.id, show.national_final_id, show.short_name, show.show_name,
               show.show_type
        FROM show
        JOIN show_types ON show_types.id = show.show_type
        WHERE show.year_id = %s
        ORDER BY show.national_final_id NULLS FIRST, show_types.sort_order,
                 show.show_number NULLS FIRST, show.id
        """,
        (year,),
    )
    progression_targets = cursor.fetchall()
    return render_template(
        "admin/create_show.html",
        years=get_years(),
        year=year,
        year_data=year_data,
        point_systems=point_systems,
        show_types=show_types,
        national_finals=national_finals,
        main_next_semifinal_number=main_next_semifinal_number,
        progression_targets=progression_targets,
        **extra,
    )


def _national_final_creation_context(year: int, **extra):
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT special_name, special_short_name FROM year WHERE id = %s", (year,)
    )
    year_data = cursor.fetchone() or {}
    cursor.execute("SELECT id, username FROM account WHERE approved ORDER BY username")
    users = cursor.fetchall()
    cursor.execute(
        """
        SELECT country.id, country.name
        FROM country
        WHERE country.id <> 'XX'
          AND NOT EXISTS (
              SELECT 1 FROM national_final
              WHERE national_final.year_id = %s
                AND national_final.owner_country_id = country.id
                AND national_final.status <> 'cancelled'
          )
        ORDER BY country.name
        """,
        (year,),
    )
    return render_template(
        "admin/create_national_final.html",
        years=get_years(),
        year=year,
        year_data=year_data,
        users=users,
        countries=cursor.fetchall(),
        **extra,
    )


@bp.route("/manage/<int(signed=True):year>/create/show", methods=["GET", "POST"])
def create_show(year: int):
    if request.method == "GET":
        return _show_creation_context(year)
    return _create_show_post(year)


@bp.route("/manage/<int(signed=True):year>/create/nf", methods=["GET", "POST"])
def create_national_final(year: int):
    if request.method == "GET":
        return _national_final_creation_context(year)
    return _create_national_final_post(year)


def _create_national_final_post(year: int):
    db = get_db()
    cursor = db.cursor()
    try:
        owner_id = int(request.form.get("owner_id", ""))
        owner_country_id = request.form.get("owner_country_id", "").strip() or None
        short_name = (
            owner_country_id.lower()
            if owner_country_id
            else request.form.get("short_name", "").strip().lower()
        )
        name = request.form.get("name", "").strip()
        if not name or not NF_SLUG_RE.fullmatch(short_name):
            raise ValueError("A national-final name and URL-safe identifier are required")
        cursor.execute(
            """
            INSERT INTO national_final (
                year_id, owner_id, owner_country_id, short_name, name
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (year, owner_id, owner_country_id, short_name, name),
        )
        if owner_country_id:
            cursor.execute(
                """
                UPDATE song SET main_participant = false
                WHERE year_id = %s AND country_id = %s
                """,
                (year, owner_country_id),
            )
        db.commit()
    except (psycopg.Error, ValueError) as exc:
        db.rollback()
        return _national_final_creation_context(
            year, error=str(exc), form=request.form
        ), 400

    return redirect(url_for("admin.create_show", year=year))


def _create_show_post(year: int):
    db = get_db()
    cur = db.cursor()
    try:
        show_type = request.form.get("show_type", "").strip()
        cur.execute("SELECT 1 FROM show_types WHERE id = %s", (show_type,))
        if not cur.fetchone():
            raise ValueError("Unknown show type")
        show_number_value = request.form.get("show_number", "").strip()
        show_number = int(show_number_value) if show_number_value else None
        if show_number is not None and (show_type != "sf" or show_number <= 0):
            raise ValueError("Only semi-finals can have a positive show number")
        short_name = f"{show_type}{show_number or ''}"

        point_system_value = request.form.get("point_system_id", "1")
        if point_system_value == "custom":
            raw_scores = request.form.get("custom_points", "")
            try:
                scores = [
                    int(value)
                    for value in re.split(r"[\s,]+", raw_scores.strip())
                    if value
                ]
            except ValueError as exc:
                raise ValueError(
                    "Custom points must be comma- or space-separated integers"
                ) from exc
            if not scores or any(score <= 0 for score in scores):
                raise ValueError("Custom points must contain positive integers")
            if len(set(scores)) != len(scores) or scores != sorted(scores, reverse=True):
                raise ValueError("Custom points must be unique and in descending order")
            cur.execute(
                """
                SELECT point_system_id
                FROM point
                GROUP BY point_system_id
                HAVING array_agg(score ORDER BY place) = %s
                LIMIT 1
                """,
                (scores,),
            )
            existing = cur.fetchone()
            if existing:
                point_system_id = existing["point_system_id"]
            else:
                cur.execute(
                    "INSERT INTO point_system (number) VALUES (%s) RETURNING id",
                    (len(scores),),
                )
                point_system_id = cur.fetchone()["id"]
                cur.executemany(
                    "INSERT INTO point (point_system_id, place, score) VALUES (%s, %s, %s)",
                    [(point_system_id, place, score) for place, score in enumerate(scores, 1)],
                )
        else:
            point_system_id = int(point_system_value)
            cur.execute("SELECT 1 FROM point_system WHERE id = %s", (point_system_id,))
            if not cur.fetchone():
                raise ValueError("Unknown point system")

        selected_event = request.form.get("national_final_id", "main").strip()
        national_final_id = None
        if selected_event != "main":
            national_final_id = selected_event
            national_final_id = int(national_final_id)
            cur.execute(
                "SELECT id FROM national_final WHERE id = %s AND year_id = %s "
                "AND status <> 'cancelled'",
                (national_final_id, year),
            )
            if not cur.fetchone():
                raise ValueError("National final not found in this year")

        composite_short_name = short_name
        if national_final_id is not None:
            cur.execute("SELECT short_name FROM national_final WHERE id = %s", (national_final_id,))
            composite_short_name = f"{cur.fetchone()['short_name']}-{short_name}"
        if national_final_id is not None:
            cur.execute(
                "SELECT 1 FROM show WHERE year_id = %s "
                "AND national_final_id IS NULL AND short_name = %s",
                (year, composite_short_name),
            )
        else:
            cur.execute(
                """
                SELECT 1 FROM show
                JOIN national_final ON national_final.id = show.national_final_id
                WHERE show.year_id = %s
                  AND national_final.short_name || '-' || show.short_name = %s
                """,
                (year, short_name),
            )
        if cur.fetchone():
            raise ValueError(
                f"The public show identifier '{composite_short_name}' is already in use"
            )

        progression_specs: list[tuple[int, int, int]] = []
        target_values = request.form.getlist("progression_target_id")
        count_values = request.form.getlist("progression_count")
        if target_values or count_values:
            if len(target_values) != len(count_values):
                raise ValueError("Invalid progression mapping")
            for target_value, count_value in zip(
                target_values, count_values, strict=True
            ):
                if not target_value and not count_value:
                    continue
                if not target_value or not count_value:
                    raise ValueError(
                        "Each progression needs a destination and qualifier count"
                    )
                count = int(count_value)
                if count <= 0:
                    raise ValueError("Qualifier counts must be positive")
                progression_specs.append(
                    (int(target_value), count, len(progression_specs) + 1)
                )

            for target_id, _count, _priority in progression_specs:
                cur.execute(
                    """
                    SELECT 1 FROM show
                    WHERE id = %s AND year_id = %s
                      AND national_final_id IS NOT DISTINCT FROM %s
                    """,
                    (target_id, year, national_final_id),
                )
                if not cur.fetchone():
                    raise ValueError("Progression destination is not in this event")

        date_value = request.form.get("date", "").strip() or None
        cur.execute(
        """
        INSERT INTO show (
            year_id, point_system_id, show_type, show_number, date,
            status, national_final_id
        ) VALUES (%s, %s, %s, %s, %s, 'none', %s)
        RETURNING id
        """,
            (year, point_system_id, show_type, show_number, date_value, national_final_id),
        )
        source_show_id = cur.fetchone()["id"]
        for target_id, count, priority in progression_specs:
            cur.execute(
                """
                INSERT INTO show_progression (
                    source_show_id, target_show_id, qualifier_count, priority
                ) VALUES (%s, %s, %s, %s)
                """,
                (source_show_id, target_id, count, priority),
            )
        db.commit()
    except (psycopg.Error, ValueError) as e:
        db.rollback()
        return _show_creation_context(year, error=str(e), form=request.form), 400

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
        SELECT show.id, show.show_name, show.short_name, show.date, show.status,
               show.voting_opens, show.voting_closes, show.predictions_close
        FROM show
        JOIN show_types ON show_types.id = show.show_type
        WHERE year_id = %s AND national_final_id IS NULL
        ORDER BY show_types.sort_order, show.show_number NULLS FIRST, show.id
    """,
        (year_id,),
    )
    shows = cursor.fetchall()
    cursor.execute(
        "SELECT short_name, name FROM national_final WHERE year_id = %s ORDER BY name",
        (year_id,),
    )
    national_finals = cursor.fetchall()
    cursor.execute(
        "SELECT id, name FROM country WHERE id <> 'XX' ORDER BY name, id"
    )
    countries = cursor.fetchall()
    return render_template(
        "admin/manage_shows.html",
        year=year_data,
        shows=shows,
        countries=countries,
        national_finals=national_finals,
    )


@bp.get("/manage/<int:year>")
def manage(year: int):
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT id, status, host_id FROM year WHERE id = %s AND id >= 0",
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
    is_form = not request.is_json
    body = request.form if is_form else request.get_json(silent=True)
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

            if status == "ongoing":
                issue = get_unassigned_lineup_issue(cursor, year, None)
                if issue:
                    error = "The contest cannot start: " + issue["message"]
                    if is_form:
                        return render_template(
                            "error.html", error=error, lineup_issues=[issue]
                        ), 400
                    return {"error": error, "lineup_issues": [issue]}, 400

            cursor.execute(
                """
                UPDATE year
                SET status = %s
                WHERE id = %s
            """,
                (status, year),
            )
        case "set_host":
            if year < 0:
                return render_template(
                    "error.html", error="Special years cannot have a host"
                ), 400

            raw_host_id = body.get("host_id")
            if raw_host_id is not None and not isinstance(raw_host_id, str):
                return render_template(
                    "error.html", error="Invalid host country"
                ), 400
            host_id = (raw_host_id or "").strip() or None
            if host_id is not None:
                cursor.execute("SELECT 1 FROM country WHERE id = %s", (host_id,))
                if not cursor.fetchone():
                    return render_template(
                        "error.html", error=f"Invalid host country '{host_id}'"
                    ), 400

                cursor.execute(
                    "SELECT id FROM show WHERE year_id = %s AND short_name = 'f'",
                    (year,),
                )
                final = cursor.fetchone()
                if not final:
                    return render_template(
                        "error.html", error=f"Final show for {year} not found"
                    ), 400

                cursor.execute(
                    """
                    SELECT id
                    FROM song
                    WHERE year_id = %s AND country_id = %s
                    ORDER BY entry_number NULLS LAST, id
                    LIMIT 1
                    """,
                    (year, host_id),
                )
                host_entry = cursor.fetchone()
                if not host_entry:
                    return render_template(
                        "error.html",
                        error=f"No {host_id} entry found for {year}",
                    ), 400

                cursor.execute(
                    """
                    INSERT INTO song_show (song_id, show_id, running_order)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (song_id, show_id) DO UPDATE
                    SET running_order = 1
                    """,
                    (host_entry["id"], final["id"]),
                )

            cursor.execute(
                "UPDATE year SET host_id = %s WHERE id = %s AND id >= 0",
                (host_id, year),
            )
            if cursor.rowcount == 0:
                return render_template("error.html", error=f"Year {year} not found"), 404
        case _:
            return render_template("error.html", error=f"Unknown action '{action}'"), 400
    db.commit()
    if is_form:
        return redirect(url_for("admin.manage", year=year))
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
                SELECT id FROM show
                WHERE year_id = %s AND short_name = %s
                  AND national_final_id IS NULL
                """,
                (year, show),
            )
            show_row = cursor.fetchone()
            if not show_row:
                return {"error": "Show not found"}, 404
            issues = get_lineup_issues(cursor, show_row["id"])
            if issues:
                return {
                    "error": "Lineup is not ready: "
                    + ", ".join(issue["message"] for issue in issues),
                    "lineup_issues": issues,
                }, 400
            cursor.execute(
                """
                UPDATE show
                SET voting_opens = COALESCE(voting_opens, CURRENT_TIMESTAMP)
                  , voting_closes = NULL
                WHERE year_id = %s AND short_name = %s
                  AND national_final_id IS NULL
            """,
                (year, show),
            )
        case "close_voting":
            cursor.execute(
                """
                UPDATE show
                SET voting_closes = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
                  AND national_final_id IS NULL
            """,
                (year, show),
            )
        case "close_predictions":
            cursor.execute(
                """
                UPDATE show
                SET predictions_close = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
                  AND national_final_id IS NULL
            """,
                (year, show),
            )
        case "open_predictions":
            cursor.execute(
                """
                UPDATE show
                SET predictions_close = NULL
                WHERE year_id = %s AND short_name = %s
                  AND national_final_id IS NULL
            """,
                (year, show),
            )
        case "set_status":
            status = body.get("status")
            if status not in ("none", "draw", "partial", "full"):
                return render_template("error.html", error="Invalid show status"), 400

            if status in ("partial", "full"):
                cursor.execute(
                    """
                    SELECT id FROM show
                    WHERE year_id = %s AND short_name = %s
                      AND national_final_id IS NULL
                    """,
                    (year, show),
                )
                show_row = cursor.fetchone()
                if not show_row:
                    return {"error": "Show not found"}, 404
                issues = get_lineup_issues(cursor, show_row["id"])
                if issues:
                    return {
                        "error": "Results cannot be published: "
                        + ", ".join(issue["message"] for issue in issues),
                        "lineup_issues": issues,
                    }, 400

            cursor.execute(
                """
                UPDATE show
                SET status = %s
                WHERE year_id = %s AND short_name = %s
                  AND national_final_id IS NULL
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
                  AND national_final_id IS NULL
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
        SELECT country.id, name, pot, genre FROM current_song AS song
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
