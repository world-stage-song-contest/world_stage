import json
import math
from datetime import UTC, datetime

from flask import request

from ...db import get_db
from ...utils import render_template
from .common import bp

EVENT_CATEGORIES = {
    "creation": "Creation",
    "deletion": "Deletion",
    "replacement": "Replacement",
    "modification": "Modification",
    "placeholder": "Placeholder",
    "status_change": "Status change",
}

FILTER_FIELDS = {
    "title": "text",
    "artist": "text",
    "native_title": "text",
    "translated_lyrics": "text",
    "romanized_lyrics": "text",
    "native_lyrics": "text",
    "video_link": "text",
    "poster_link": "text",
    "vtt_link": "text",
    "duration": "numeric",
    "snippet_start": "numeric",
    "snippet_end": "numeric",
    "snippet2_start": "numeric",
    "snippet2_end": "numeric",
    "language_set_id": "numeric",
    "title_language_id": "numeric",
    "native_language_id": "numeric",
    "submitter_id": "numeric",
    "notes": "text",
    "sources": "text",
    "approval_status": "text",
    "is_placeholder": "boolean",
}
CHOICE_FIELDS = {
    "language_set_id",
    "title_language_id",
    "native_language_id",
    "submitter_id",
    "approval_status",
}
TEXT_PATTERN_FIELDS = {
    field
    for field, field_type in FILTER_FIELDS.items()
    if field_type == "text" and field not in CHOICE_FIELDS
}
NUMERIC_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "lt": "<",
    "gt": ">",
    "lte": "<=",
    "gte": ">=",
}
TEXT_OPERATORS = {
    "eq": ("", ""),
    "starts_with": ("", "%"),
    "ends_with": ("%", ""),
    "contains": ("%", "%"),
}


def _filter_field_config(cursor):
    config = {field: {"type": field_type} for field, field_type in FILTER_FIELDS.items()}

    cursor.execute("SELECT id, username FROM account ORDER BY username, id")
    config["submitter_id"]["choices"] = [
        {"value": row["id"], "label": row["username"] or f"Account {row['id']}"}
        for row in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT language_set.id,
               STRING_AGG(language.name, ', ' ORDER BY member.priority) AS label
        FROM language_set
        JOIN language_set_language AS member
          ON member.language_set_id = language_set.id
        JOIN language ON language.id = member.language_id
        GROUP BY language_set.id
        ORDER BY label, language_set.id
        """
    )
    config["language_set_id"]["choices"] = [
        {"value": row["id"], "label": row["label"]} for row in cursor.fetchall()
    ]

    cursor.execute("SELECT id, name FROM language ORDER BY name, id")
    language_choices = [
        {"value": row["id"], "label": row["name"]} for row in cursor.fetchall()
    ]
    config["title_language_id"]["choices"] = language_choices
    config["native_language_id"]["choices"] = language_choices

    cursor.execute("SELECT name FROM song_approval_status ORDER BY name")
    config["approval_status"]["choices"] = [
        {"value": row["name"], "label": row["name"]} for row in cursor.fetchall()
    ]
    return config


def _parse_filters():
    selected_categories = [
        value
        for value in request.args.getlist("events")
        if value in EVENT_CATEGORIES
    ]
    if not selected_categories:
        selected_categories = list(EVENT_CATEGORIES)

    try:
        requested_filters = json.loads(request.args.get("filters", "[]"))
    except (TypeError, ValueError):
        requested_filters = []
    if not isinstance(requested_filters, list):
        requested_filters = []

    selected_filters = []
    for item in requested_filters:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        if field not in FILTER_FIELDS:
            continue
        join = item.get("join", "and")
        negate = item.get("not", False)
        if join not in {"and", "or"} or not isinstance(negate, bool):
            continue
        parsed = {"field": field}
        if selected_filters:
            parsed["join"] = join
        if negate:
            parsed["not"] = True
        valid = True
        for boundary in ("from", "to"):
            if boundary not in item:
                continue
            value = item[boundary]
            field_type = FILTER_FIELDS[field]
            if field_type == "boolean" and not isinstance(value, bool):
                valid = False
            if field_type == "numeric" and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                valid = False
            if (
                field_type == "numeric"
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and not math.isfinite(value)
            ):
                valid = False
            if field_type == "text" and not isinstance(value, str):
                valid = False
            parsed[boundary] = value
            if field in TEXT_PATTERN_FIELDS:
                operator = item.get(f"{boundary}_operator", "eq")
                case_sensitive = item.get(f"{boundary}_case_sensitive", False)
                accent_sensitive = item.get(f"{boundary}_accent_sensitive", False)
                if operator not in TEXT_OPERATORS or not isinstance(
                    case_sensitive, bool
                ) or not isinstance(accent_sensitive, bool):
                    valid = False
                else:
                    parsed[f"{boundary}_operator"] = operator
                    parsed[f"{boundary}_case_sensitive"] = case_sensitive
                    parsed[f"{boundary}_accent_sensitive"] = accent_sensitive
            elif field_type == "numeric" and field not in CHOICE_FIELDS:
                operator = item.get(f"{boundary}_operator", "eq")
                if operator not in NUMERIC_OPERATORS:
                    valid = False
                else:
                    parsed[f"{boundary}_operator"] = operator
        if valid:
            selected_filters.append(parsed)

    return selected_categories, selected_filters


def _filter_expression(selected_filters):
    if not selected_filters:
        return "TRUE", []

    clauses = []
    params = []
    for index, selected_filter in enumerate(selected_filters):
        field = selected_filter["field"]
        changed = "COALESCE(raw.changed_fields, '{}'::jsonb) ? %s"
        changed_params = [field]
        constraints = []
        constraint_params = []

        for boundary, json_key in (("from", "old"), ("to", "new")):
            if boundary not in selected_filter:
                continue
            if field in TEXT_PATTERN_FIELDS:
                prefix, suffix = TEXT_OPERATORS[
                    selected_filter.get(f"{boundary}_operator", "eq")
                ]
                comparison = (
                    "LIKE"
                    if selected_filter.get(f"{boundary}_case_sensitive", False)
                    else "ILIKE"
                )
                field_expression = (
                    f"raw.changed_fields -> %s ->> '{json_key}'"
                )
                value_expression = "%s"
                if not selected_filter.get(f"{boundary}_accent_sensitive", False):
                    field_expression = f"unaccent({field_expression})"
                    value_expression = "unaccent(%s)"
                constraints.append(
                    f"{field_expression} {comparison} {value_expression}"
                )
                constraint_params.extend(
                    [field, f"{prefix}{selected_filter[boundary]}{suffix}"]
                )
            elif FILTER_FIELDS[field] == "numeric" and field not in CHOICE_FIELDS:
                operator = NUMERIC_OPERATORS[
                    selected_filter.get(f"{boundary}_operator", "eq")
                ]
                constraints.append(
                    f"(raw.changed_fields -> %s ->> '{json_key}')::double precision "
                    f"{operator} %s"
                )
                constraint_params.extend([field, selected_filter[boundary]])
            else:
                constraints.append(
                    f"raw.changed_fields -> %s -> '{json_key}' = %s::jsonb"
                )
                constraint_params.extend(
                    [field, json.dumps(selected_filter[boundary], separators=(",", ":"))]
                )

        if constraints:
            value_expression = " AND ".join(f"({clause})" for clause in constraints)
            if selected_filter.get("not"):
                value_expression = f"NOT ({value_expression})"
            clause = f"({changed}) AND ({value_expression})"
            clause_params = changed_params + constraint_params
        elif selected_filter.get("not"):
            clause = f"NOT ({changed})"
            clause_params = changed_params
        else:
            clause = changed
            clause_params = changed_params

        if index:
            clauses.append(selected_filter.get("join", "and").upper())
        clauses.append(f"({clause})")
        params.extend(clause_params)

    return " ".join(clauses), params


def _describe_change(entry, username_map):
    changed_fields = entry["changed_fields"] or {}
    details = []

    for field in ("title", "artist"):
        if change := changed_fields.get(field):
            details.append(
                f"{field.title()}: {change['old'] or ''} → {change['new'] or ''}"
            )

    if change := changed_fields.get("submitter_id"):
        details.append(
            f"submitter_id: {username_map.get(change['old'], '—')} → "
            f"{username_map.get(change['new'], '—')}"
        )

    for field in ("approval_status", "is_placeholder"):
        if change := changed_fields.get(field):
            old = str(change["old"]).lower()
            new = str(change["new"]).lower()
            details.append(f"{field}: {old} → {new}")

    elaborated_fields = {
        "title", "artist", "submitter_id", "approval_status", "is_placeholder"
    }
    simple_fields = sorted(
        field for field in changed_fields if field not in elaborated_fields
    )
    if simple_fields:
        details.append(", ".join(simple_fields))

    return details


def _timestamp_parameter(name):
    value = request.args.get(name, "")
    if not value:
        return "", None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return "", None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return value, parsed


@bp.get("/changes")
def changes():
    db = get_db()
    cursor = db.cursor()
    per_page = 25

    selected_categories, selected_filters = _parse_filters()
    serialized_filters = json.dumps(selected_filters, separators=(",", ":"))
    filter_expression, filter_params = _filter_expression(selected_filters)

    before, before_at = _timestamp_parameter("before")
    from_time, from_time_at = _timestamp_parameter("from_time")
    to_time, to_time_at = _timestamp_parameter("to_time")

    cursor.execute(
        """
        WITH status_history AS (
            SELECT status.*,
                   LAG(status.approval_status) OVER status_order AS previous_approval,
                   LAG(status.is_placeholder) OVER status_order AS previous_placeholder,
                   ROW_NUMBER() OVER status_order AS status_number
            FROM song_status AS status
            WINDOW status_order AS (
                PARTITION BY status.song_id
                ORDER BY status.created_at, status.id
            )
        ), raw_changes AS (
            SELECT
                'd'::text AS source,
                0 AS source_order,
                change.id,
                change.changed_at,
                change.song_id,
                change.song_title,
                change.song_artist,
                change.song_country_id,
                change.song_year_id,
                change.changed_fields,
                change.changed_by,
                CASE
                    WHEN change.event_type = 'create' THEN ARRAY['creation']::text[]
                    WHEN change.event_type = 'delete' THEN ARRAY['deletion']::text[]
                    WHEN change.event_type = 'song_replacement'
                        THEN ARRAY['replacement']::text[]
                    ELSE ARRAY['modification']::text[]
                END AS event_categories
            FROM song_change AS change

            UNION ALL

            SELECT
                's'::text AS source,
                1 AS source_order,
                status.id,
                status.created_at AS changed_at,
                data.song_id,
                data.title AS song_title,
                data.artist AS song_artist,
                data.country_id AS song_country_id,
                data.year_id AS song_year_id,
                JSONB_STRIP_NULLS(JSONB_BUILD_OBJECT(
                    'approval_status', CASE
                        WHEN status.approval_status
                             IS DISTINCT FROM status.previous_approval
                        THEN JSONB_BUILD_OBJECT(
                            'old', status.previous_approval,
                            'new', status.approval_status
                        ) END,
                    'is_placeholder', CASE
                        WHEN status.is_placeholder
                             IS DISTINCT FROM status.previous_placeholder
                        THEN JSONB_BUILD_OBJECT(
                            'old', status.previous_placeholder,
                            'new', status.is_placeholder
                        ) END
                )) AS changed_fields,
                status.changed_by,
                ARRAY_REMOVE(ARRAY[
                    CASE WHEN status.approval_status
                                   IS DISTINCT FROM status.previous_approval
                         THEN 'status_change' END,
                    CASE WHEN status.is_placeholder
                                   IS DISTINCT FROM status.previous_placeholder
                         THEN 'placeholder' END
                ], NULL) AS event_categories
            FROM status_history AS status
            JOIN song_data AS data ON data.id = status.song_data_id
            WHERE status.status_number > 1
              AND status.changed_by IS NOT NULL
              AND (
                  status.approval_status IS DISTINCT FROM status.previous_approval
                  OR status.is_placeholder IS DISTINCT FROM status.previous_placeholder
              )
        ), filtered_changes AS (
            SELECT raw.*
            FROM raw_changes AS raw
            WHERE raw.event_categories && %s::text[]
              AND (%s::timestamptz IS NULL OR raw.changed_at < %s::timestamptz)
              AND (%s::timestamptz IS NULL OR raw.changed_at >= %s::timestamptz)
              AND (%s::timestamptz IS NULL OR raw.changed_at <= %s::timestamptz)
              AND ("""
        + filter_expression
        + """)
        ), ranked_changes AS (
            SELECT filtered_changes.*,
                   ROW_NUMBER() OVER (
                       ORDER BY changed_at DESC, id DESC, source_order DESC
                   ) AS position,
                   COUNT(*) OVER () AS total_count
            FROM filtered_changes
        )
        SELECT ranked.*, a.username AS changed_by_username,
               c.name AS country_name
        FROM ranked_changes AS ranked
        LEFT JOIN account a ON a.id = ranked.changed_by
        LEFT JOIN country c ON c.id = ranked.song_country_id
        WHERE ranked.position <= %s
           OR ranked.changed_at = (
               SELECT boundary.changed_at
               FROM ranked_changes AS boundary
               WHERE boundary.position = %s
           )
        ORDER BY ranked.changed_at DESC, ranked.id DESC, ranked.source_order DESC
        """,
        (
            selected_categories,
            before_at,
            before_at,
            from_time_at,
            from_time_at,
            to_time_at,
            to_time_at,
            *filter_params,
            per_page,
            per_page,
        ),
    )
    audit_changes = cursor.fetchall()
    has_older = bool(audit_changes) and audit_changes[0]["total_count"] > len(
        audit_changes
    )
    next_before = audit_changes[-1]["changed_at"].isoformat() if has_older else None

    submitter_ids = set()
    for entry in audit_changes:
        change = (entry["changed_fields"] or {}).get("submitter_id")
        if change:
            submitter_ids.update(value for value in change.values() if value is not None)
    username_map = {}
    if submitter_ids:
        cursor.execute(
            "SELECT id, username FROM account WHERE id = ANY(%s)",
            ([int(value) for value in submitter_ids],),
        )
        for row in cursor.fetchall():
            username_map[row["id"]] = row["username"]
            username_map[str(row["id"])] = row["username"]

    for entry in audit_changes:
        entry["change_details"] = _describe_change(entry, username_map)

    return render_template(
        "admin/changes.html",
        changes=audit_changes,
        before=before,
        next_before=next_before,
        from_time=from_time,
        to_time=to_time,
        event_categories=EVENT_CATEGORIES,
        filter_fields=_filter_field_config(cursor),
        selected_events=selected_categories,
        selected_filters=selected_filters,
        serialized_filters=serialized_filters,
    )
