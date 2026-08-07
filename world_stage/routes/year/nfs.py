import re

import psycopg
from flask import redirect, request, url_for

from ...db import get_db
from ...utils import (
    UserPermissions,
    get_lineup_issues,
    get_unassigned_lineup_issue,
    render_template,
    with_auth,
)
from .common import bp, resolve_special

NF_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _national_final(year_id: int, short_name: str) -> dict | None:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT national_final.*, account.username AS owner_username,
               country.name AS owner_country_name
        FROM national_final
        JOIN account ON account.id = national_final.owner_id
        LEFT JOIN country ON country.id = national_final.owner_country_id
        WHERE national_final.year_id = %s AND national_final.short_name = %s
        """,
        (year_id, short_name),
    )
    return cursor.fetchone()


def _can_manage(nf: dict, user, permissions: UserPermissions) -> bool:
    return bool(permissions.can_view_restricted or (user is not None and user[0] == nf["owner_id"]))


def _archive_results(cursor, nf: dict) -> tuple[dict[int, dict], dict[int, dict[int, dict]]]:
    cursor.execute(
        """
        SELECT result.song_id, result.show_id, result.place, result.total_points,
               result.points_percentage, result.entry_status,
               result.special_qualifier
        FROM country_show_results AS result
        JOIN show ON show.id = result.show_id
        WHERE show.national_final_id = %s
          AND show.status = 'full'
          AND result.result_mode = 'official'
        ORDER BY show.id, result.place
        """,
        (nf["id"],),
    )
    stage_results: dict[int, dict[int, dict]] = {}
    for result in cursor.fetchall():
        stage_results.setdefault(result["song_id"], {})[result["show_id"]] = result

    if nf["status"] != "finished":
        return {}, stage_results

    cursor.execute(
        """
        WITH RECURSIVE scoped_progression AS (
            SELECT progression.source_show_id, progression.target_show_id
            FROM show_progression AS progression
            JOIN show AS source ON source.id = progression.source_show_id
            JOIN show AS target ON target.id = progression.target_show_id
            WHERE source.national_final_id = %s
              AND target.national_final_id = %s
        ), paths(source_show_id, target_show_id, distance) AS (
            SELECT source_show_id, target_show_id, 1
            FROM scoped_progression
            UNION ALL
            SELECT paths.source_show_id, progression.target_show_id,
                   paths.distance + 1
            FROM paths
            JOIN scoped_progression AS progression
              ON progression.source_show_id = paths.target_show_id
        ), show_tiers AS (
            SELECT show.id AS show_id, COALESCE(MAX(paths.distance), 0) + 1 AS tier
            FROM show
            LEFT JOIN paths ON paths.source_show_id = show.id
            WHERE show.national_final_id = %s AND show.status = 'full'
            GROUP BY show.id
        ), candidates AS (
            SELECT result.song_id, result.show_id, result.place AS show_place,
                   result.total_points, result.points_percentage,
                   result.total_votes_received, result.running_order,
                   show.short_name AS reached_show, show.show_name AS reached_show_name,
                   show_tiers.tier,
                   STRING_AGG(
                       LPAD(points.key, 3, '0') || ':' || LPAD(points.value, 3, '0'),
                       ',' ORDER BY points.key::integer DESC
                   ) AS countback,
                   ROW_NUMBER() OVER (
                       PARTITION BY result.song_id
                       ORDER BY show_tiers.tier, result.place, result.show_id
                   ) AS reached_order
            FROM country_show_results AS result
            JOIN show_tiers ON show_tiers.show_id = result.show_id
            JOIN show ON show.id = result.show_id
            LEFT JOIN LATERAL
                JSONB_EACH_TEXT(result.point_distribution) AS points ON true
            WHERE result.result_mode = 'official'
            GROUP BY result.song_id, result.show_id, result.place,
                     result.total_points, result.points_percentage,
                     result.total_votes_received, result.running_order,
                     show.short_name, show.show_name, show_tiers.tier
        ), reached AS (
            SELECT * FROM candidates WHERE reached_order = 1
        )
        SELECT reached.*,
               ROW_NUMBER() OVER (
                   ORDER BY tier, points_percentage DESC,
                            total_votes_received DESC, countback DESC NULLS LAST,
                            running_order NULLS LAST, show_id, song_id
               )::integer AS overall_place
        FROM reached
        ORDER BY overall_place
        """,
        (nf["id"], nf["id"], nf["id"]),
    )
    rankings = {result["song_id"]: result for result in cursor.fetchall()}
    return rankings, stage_results


def _list_nfs(year_id: int, year_label: str, *, special: str | None = None):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT national_final.id, national_final.short_name, national_final.name,
               national_final.status,
               national_final.owner_country_id, country.name AS owner_country_name,
               account.username AS owner_username, COUNT(show.id) AS show_count,
               MIN(show.date) AS first_date, MAX(show.date) AS last_date
        FROM national_final
        JOIN account ON account.id = national_final.owner_id
        LEFT JOIN country ON country.id = national_final.owner_country_id
        LEFT JOIN show ON show.national_final_id = national_final.id
        WHERE national_final.year_id = %s
        GROUP BY national_final.id, country.id, account.id
        ORDER BY COALESCE(MIN(show.date), national_final.created_at::date), national_final.name
        """,
        (year_id,),
    )
    return render_template(
        "year/nfs/index.html",
        national_finals=cursor.fetchall(),
        year=year_id,
        year_label=year_label,
        special=special,
    )


@bp.get("/<int:year>/nfs")
def nfs(year: int):
    return _list_nfs(year, str(year))


@bp.get("/special/<short_name>/nfs")
def special_nfs(short_name: str):
    special = resolve_special(short_name)
    if not special:
        return render_template("error.html", error="Special not found"), 404
    return _list_nfs(special["id"], special["special_name"] or short_name, special=short_name)


def _show_nf(
    year_id: int,
    year_label: str,
    nf_short_name: str,
    user,
    permissions: UserPermissions,
    *,
    special: str | None = None,
    management: bool = False,
):
    nf = _national_final(year_id, nf_short_name)
    if not nf:
        return render_template("error.html", error="National final not found"), 404
    can_manage = _can_manage(nf, user, permissions)
    if management and not can_manage:
        return render_template("error.html", error="Not authorized"), 403
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show.id, show.short_name, show.show_name, show.date, show.status,
               show.voting_opens, show.voting_closes,
               array_agg(point.score ORDER BY point.place) AS points
        FROM show
        JOIN show_types ON show_types.id = show.show_type
        JOIN point ON point.point_system_id = show.point_system_id
        WHERE show.national_final_id = %s
        GROUP BY show.id, show_types.sort_order
        ORDER BY show_types.sort_order, show.show_number NULLS FIRST, show.id
        """,
        (nf["id"],),
    )
    shows = cursor.fetchall()
    cursor.execute(
        """
        SELECT song.id, song.country_id, country.name AS country,
               song.entry_number, song.title, song.artist, song.main_participant,
               account.username AS submitter
        FROM national_final_song
        JOIN current_song AS song ON song.id = national_final_song.song_id
        JOIN country ON country.id = song.country_id
        LEFT JOIN account ON account.id = song.submitter_id
        WHERE national_final_song.national_final_id = %s
        ORDER BY country.name, song.entry_number, song.id
        """,
        (nf["id"],),
    )
    candidates = cursor.fetchall()
    rankings, stage_results = _archive_results(cursor, nf)
    if rankings:
        candidates.sort(
            key=lambda candidate: (
                rankings.get(candidate["id"], {}).get("overall_place", len(candidates) + 1),
                candidate["country"],
                candidate["entry_number"],
            )
        )
    cursor.execute(
        """
        SELECT song_show.song_id, show.short_name, song_show.running_order
        FROM song_show
        JOIN show ON show.id = song_show.show_id
        JOIN show_types ON show_types.id = show.show_type
        WHERE show.national_final_id = %s
        ORDER BY show_types.sort_order, show.show_number NULLS FIRST,
                 show.id, song_show.running_order
        """,
        (nf["id"],),
    )
    assignments: dict[int, list[dict]] = {}
    for row in cursor.fetchall():
        assignments.setdefault(row["song_id"], []).append(row)
    metadata_countries = []
    metadata_owners = []
    if management:
        cursor.execute(
            """
            SELECT country.id, country.name
            FROM country
            WHERE country.id <> 'XX'
              AND (
                  country.id = %s
                  OR NOT EXISTS (
                      SELECT 1
                      FROM national_final AS other
                      WHERE other.year_id = %s
                        AND other.owner_country_id = country.id
                        AND other.status <> 'cancelled'
                        AND other.id <> %s
                  )
              )
            ORDER BY country.name
            """,
            (nf["owner_country_id"], year_id, nf["id"]),
        )
        metadata_countries = cursor.fetchall()
        if permissions.can_view_restricted:
            cursor.execute(
                "SELECT id, username FROM account WHERE approved ORDER BY username"
            )
            metadata_owners = cursor.fetchall()
    return render_template(
        "year/nfs/manage.html" if management else "year/nfs/details.html",
        nf=nf,
        shows=shows,
        candidates=candidates,
        rankings=rankings,
        stage_results=stage_results,
        assignments=assignments,
        metadata_countries=metadata_countries,
        metadata_owners=metadata_owners,
        can_reassign_owner=permissions.can_view_restricted,
        can_manage=can_manage,
        year=year_id,
        year_label=year_label,
        special=special,
    )


@bp.get("/<int:year>/nfs/<nf_short_name>")
@with_auth
def nf_details(year: int, nf_short_name: str, user, permissions: UserPermissions):
    return _show_nf(year, str(year), nf_short_name, user, permissions)


@bp.get("/special/<short_name>/nfs/<nf_short_name>")
@with_auth
def special_nf_details(short_name: str, nf_short_name: str, user, permissions: UserPermissions):
    special = resolve_special(short_name)
    if not special:
        return render_template("error.html", error="Special not found"), 404
    return _show_nf(
        special["id"],
        special["special_name"] or short_name,
        nf_short_name,
        user,
        permissions,
        special=short_name,
    )


def _manage_nf(year_id: int, nf_short_name: str, user, permissions: UserPermissions):
    nf = _national_final(year_id, nf_short_name)
    if not nf:
        return render_template("error.html", error="National final not found"), 404
    if not _can_manage(nf, user, permissions):
        return render_template("error.html", error="Not authorized"), 403
    action = request.form.get("action", "")
    db = get_db()
    cursor = db.cursor()

    if action == "update_metadata":
        try:
            name = request.form.get("name", "").strip()
            owner_country_id = request.form.get("owner_country_id", "").strip() or None
            short_name = (
                owner_country_id.lower()
                if owner_country_id
                else request.form.get("short_name", "").strip().lower()
            )
            owner_id = nf["owner_id"]
            if permissions.can_view_restricted:
                owner_id = request.form.get("owner_id", type=int)
                cursor.execute(
                    "SELECT 1 FROM account WHERE id = %s AND approved", (owner_id,)
                )
                if not cursor.fetchone():
                    raise ValueError("Select an approved owner")
            if not name or not NF_SLUG_RE.fullmatch(short_name):
                raise ValueError(
                    "A national-final name and URL-safe identifier are required"
                )
            if owner_country_id:
                cursor.execute(
                    """
                    SELECT 1
                    FROM national_final_song
                    JOIN song ON song.id = national_final_song.song_id
                    WHERE national_final_song.national_final_id = %s
                      AND song.country_id <> %s
                    LIMIT 1
                    """,
                    (nf["id"], owner_country_id),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "The selected country does not match every existing candidate"
                    )
            cursor.execute(
                """
                UPDATE national_final
                SET name = %s, owner_id = %s, owner_country_id = %s, short_name = %s
                WHERE id = %s
                """,
                (name, owner_id, owner_country_id, short_name, nf["id"]),
            )
            if owner_country_id and owner_country_id != nf["owner_country_id"]:
                cursor.execute(
                    """
                    UPDATE song SET main_participant = false
                    WHERE year_id = %s AND country_id = %s
                    """,
                    (year_id, owner_country_id),
                )
            db.commit()
            return short_name
        except (psycopg.Error, ValueError) as exc:
            db.rollback()
            return render_template("error.html", error=str(exc)), 400
    elif action == "set_lifecycle":
        status = request.form.get("lifecycle_status")
        if status not in {"draft", "submissions", "voting", "finished", "cancelled"}:
            return render_template("error.html", error="Invalid national final status"), 400
        if status == "voting":
            issue = get_unassigned_lineup_issue(cursor, year_id, nf["id"])
            if issue:
                return render_template(
                    "error.html",
                    error="The national final cannot start: " + issue["message"],
                    lineup_issues=[issue],
                ), 400
        cursor.execute("UPDATE national_final SET status = %s WHERE id = %s", (status, nf["id"]))
        if status != "voting":
            cursor.execute(
                "UPDATE show SET voting_closes = CURRENT_TIMESTAMP "
                "WHERE national_final_id = %s AND voting_opens IS NOT NULL "
                "AND voting_closes IS NULL",
                (nf["id"],),
            )
    elif action in {"open_voting", "close_voting", "set_status"}:
        show_id = request.form.get("show_id", type=int)
        cursor.execute(
            "SELECT id FROM show WHERE id = %s AND national_final_id = %s",
            (show_id, nf["id"]),
        )
        if not cursor.fetchone():
            return render_template("error.html", error="Show not found"), 404
        if action == "open_voting":
            if nf["status"] == "cancelled":
                return render_template(
                    "error.html", error="A cancelled national final cannot be opened for voting"
                ), 400
            issues = get_lineup_issues(cursor, show_id)
            if nf["status"] != "voting":
                unassigned_issue = get_unassigned_lineup_issue(
                    cursor, year_id, nf["id"]
                )
                if unassigned_issue:
                    issues.append(unassigned_issue)
            if issues:
                return render_template(
                    "error.html",
                    error="Lineup is not ready: "
                    + ", ".join(issue["message"] for issue in issues),
                    lineup_issues=issues,
                ), 400
            cursor.execute(
                "UPDATE show SET voting_opens = COALESCE(voting_opens, CURRENT_TIMESTAMP), "
                "voting_closes = NULL WHERE id = %s",
                (show_id,),
            )
            cursor.execute(
                "UPDATE national_final SET status = 'voting' WHERE id = %s", (nf["id"],)
            )
        elif action == "close_voting":
            cursor.execute(
                "UPDATE show SET voting_closes = CURRENT_TIMESTAMP WHERE id = %s", (show_id,)
            )
        else:
            status = request.form.get("status")
            if status not in {"none", "draw", "partial", "full"}:
                return render_template("error.html", error="Invalid show status"), 400
            if status in {"partial", "full"}:
                issues = get_lineup_issues(cursor, show_id)
                if issues:
                    return render_template(
                        "error.html",
                        error="Results cannot be published: "
                        + ", ".join(issue["message"] for issue in issues),
                        lineup_issues=issues,
                    ), 400
                cursor.execute("SELECT voting_closes FROM show WHERE id = %s", (show_id,))
                if cursor.fetchone()["voting_closes"] is None:
                    return render_template(
                        "error.html", error="Close voting before revealing results"
                    ), 400
            cursor.execute("UPDATE show SET status = %s WHERE id = %s", (status, show_id))
    elif action in {"assign", "remove", "set_order"}:
        show_id = request.form.get("show_id", type=int)
        song_id = request.form.get("song_id", type=int)
        cursor.execute(
            """
            SELECT 1 FROM show
            JOIN national_final_song
              ON national_final_song.national_final_id = show.national_final_id
            WHERE show.id = %s AND show.national_final_id = %s
              AND national_final_song.song_id = %s
            """,
            (show_id, nf["id"], song_id),
        )
        if not cursor.fetchone():
            return render_template("error.html", error="Invalid show or candidate"), 400
        cursor.execute("SELECT 1 FROM vote_set WHERE show_id = %s LIMIT 1", (show_id,))
        if cursor.fetchone():
            return render_template("error.html", error="The lineup is locked after voting"), 400
        if action == "assign":
            cursor.execute(
                "SELECT COALESCE(MAX(running_order), 0) + 1 AS next "
                "FROM song_show WHERE show_id = %s",
                (show_id,),
            )
            running_order = cursor.fetchone()["next"]
            cursor.execute(
                """
                INSERT INTO song_show (song_id, show_id, running_order)
                VALUES (%s, %s, %s)
                ON CONFLICT (song_id, show_id) DO UPDATE
                SET running_order = EXCLUDED.running_order
                """,
                (song_id, show_id, running_order),
            )
        elif action == "remove":
            cursor.execute(
                "DELETE FROM song_show WHERE song_id = %s AND show_id = %s",
                (song_id, show_id),
            )
        else:
            running_order = request.form.get("running_order", type=int)
            if running_order is None or running_order < 1:
                return render_template("error.html", error="Invalid running order"), 400
            cursor.execute(
                "UPDATE song_show SET running_order = %s WHERE song_id = %s AND show_id = %s",
                (running_order, song_id, show_id),
            )
    elif action == "set_main_participant":
        song_id = request.form.get("song_id", type=int)
        enabled = request.form.get("enabled") == "true"
        cursor.execute(
            """
            SELECT song.id, song.country_id
            FROM song
            JOIN national_final_song ON national_final_song.song_id = song.id
            WHERE song.id = %s AND national_final_song.national_final_id = %s
            """,
            (song_id, nf["id"]),
        )
        song = cursor.fetchone()
        if not song:
            return render_template("error.html", error="Candidate not found"), 404
        if enabled and year_id >= 0:
            cursor.execute(
                "UPDATE song SET main_participant = false "
                "WHERE year_id = %s AND country_id = %s AND id <> %s",
                (year_id, song["country_id"], song_id),
            )
        cursor.execute("UPDATE song SET main_participant = %s WHERE id = %s", (enabled, song_id))
    else:
        return render_template("error.html", error="Unknown action"), 400

    db.commit()
    return None


@bp.route("/<int:year>/nfs/<nf_short_name>/manage", methods=["GET", "POST"])
@with_auth
def manage_nf(year: int, nf_short_name: str, user, permissions: UserPermissions):
    if request.method == "GET":
        return _show_nf(
            year, str(year), nf_short_name, user, permissions, management=True
        )
    result = _manage_nf(year, nf_short_name, user, permissions)
    if result is not None and not isinstance(result, str):
        return result
    return redirect(
        url_for("year.manage_nf", year=year, nf_short_name=result or nf_short_name)
    )


@bp.route(
    "/special/<short_name>/nfs/<nf_short_name>/manage", methods=["GET", "POST"]
)
@with_auth
def manage_special_nf(short_name: str, nf_short_name: str, user, permissions: UserPermissions):
    special = resolve_special(short_name)
    if not special:
        return render_template("error.html", error="Special not found"), 404
    if request.method == "GET":
        return _show_nf(
            special["id"],
            special["special_name"] or short_name,
            nf_short_name,
            user,
            permissions,
            special=short_name,
            management=True,
        )
    result = _manage_nf(special["id"], nf_short_name, user, permissions)
    if result is not None and not isinstance(result, str):
        return result
    return redirect(
        url_for(
            "year.manage_special_nf",
            short_name=short_name,
            nf_short_name=result or nf_short_name,
        )
    )
