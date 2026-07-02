import math

from flask import request

from ...db import fetchone, get_db
from ...utils import (
    render_template,
)
from .common import bp

ALL_EVENT_TYPES = [
    "create",
    "delete",
    "song_replacement",
    "song_modification",
    "placeholder_on",
    "placeholder_off",
    "ownership_change",
]


@bp.get("/changes")
def changes():
    db = get_db()
    cursor = db.cursor()

    per_page = 250

    # Event type filtering
    selected_events = request.args.getlist("events")
    if not selected_events:
        selected_events = list(ALL_EVENT_TYPES)
    # Ensure only valid event types
    selected_events = [e for e in selected_events if e in ALL_EVENT_TYPES]
    if not selected_events:
        selected_events = list(ALL_EVENT_TYPES)

    # Count total matching entries
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM song_audit_log WHERE event_type = ANY(%s)", (selected_events,)
    )
    total = fetchone(cursor)["cnt"]
    total_pages = max(1, math.ceil(total / per_page))

    page = request.args.get("page", 1, type=int)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    cursor.execute(
        """
        SELECT
            sal.id,
            sal.event_type,
            sal.changed_at,
            sal.song_id,
            sal.song_title,
            sal.song_artist,
            sal.song_country_id,
            sal.song_year_id,
            sal.changed_fields,
            a.username  AS changed_by_username,
            c.name      AS country_name
        FROM song_audit_log sal
        LEFT JOIN account a ON a.id = sal.changed_by
        LEFT JOIN country c ON c.id = sal.song_country_id
        WHERE sal.event_type = ANY(%s)
        ORDER BY sal.changed_at DESC
        LIMIT %s OFFSET %s
    """,
        (selected_events, per_page, offset),
    )
    changes = cursor.fetchall()

    # Resolve submitter IDs to usernames for ownership_change entries.
    submitter_ids: set[int] = set()
    for entry in changes:
        if entry["event_type"] == "ownership_change" and entry["changed_fields"]:
            cf = entry["changed_fields"]
            if "submitter_id" in cf:
                for key in ("old", "new"):
                    val = cf["submitter_id"].get(key)
                    if val is not None:
                        submitter_ids.add(int(val))
    username_map: dict = {}
    if submitter_ids:
        cursor.execute(
            "SELECT id, username FROM account WHERE id = ANY(%s)", (list(submitter_ids),)
        )
        for row in cursor.fetchall():
            username_map[row["id"]] = row["username"]
            username_map[str(row["id"])] = row["username"]

    return render_template(
        "admin/changes.html",
        changes=changes,
        username_map=username_map,
        page=page,
        total_pages=total_pages,
        total=total,
        all_event_types=ALL_EVENT_TYPES,
        selected_events=selected_events,
    )
