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
    "country_id": "text",
    "year_id": "numeric",
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
    "genre_set_id": "numeric",
    "key_signature_set_id": "numeric",
    "time_signature_set_id": "numeric",
    "title_language_id": "numeric",
    "native_language_id": "numeric",
    "submitter_id": "numeric",
    "notes": "text",
    "sources": "text",
    "approval_status": "text",
    "is_placeholder": "boolean",
}
CHOICE_FIELDS = {
    "country_id",
    "year_id",
    "language_set_id",
    "genre_set_id",
    "key_signature_set_id",
    "time_signature_set_id",
    "title_language_id",
    "native_language_id",
    "submitter_id",
    "approval_status",
}
IDENTITY_FIELDS = {"country_id", "year_id"}
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
    cursor.execute(
        """
        SELECT
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'value', country.id,
                        'label', COALESCE(country.name, country.id)
                    ) ORDER BY country.name, country.id
                )
                FROM country
            ), '[]'::jsonb) AS countries,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'value', year.id,
                        'label', COALESCE(year.special_name, year.id::text)
                    ) ORDER BY year.id DESC
                )
                FROM year
            ), '[]'::jsonb) AS years,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'value', account.id,
                        'label', COALESCE(
                            account.username,
                            'Account ' || account.id::text
                        )
                    ) ORDER BY account.username, account.id
                )
                FROM account
            ), '[]'::jsonb) AS accounts,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object('value', choices.id, 'label', choices.label)
                    ORDER BY choices.label, choices.id
                )
                FROM (
                    SELECT language_set.id,
                           STRING_AGG(
                               language.name, ', ' ORDER BY member.priority
                           ) AS label
                    FROM language_set
                    JOIN language_set_language member
                      ON member.language_set_id = language_set.id
                    JOIN language ON language.id = member.language_id
                    GROUP BY language_set.id
                ) choices
            ), '[]'::jsonb) AS language_sets,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object('value', choices.id, 'label', choices.label)
                    ORDER BY choices.label, choices.id
                )
                FROM (
                    SELECT genre_set.id,
                           STRING_AGG(
                               subgenre.name, ', ' ORDER BY member.priority
                           ) AS label
                    FROM genre_set
                    JOIN genre_set_subgenre member
                      ON member.genre_set_id = genre_set.id
                    JOIN subgenre ON subgenre.id = member.subgenre_id
                    GROUP BY genre_set.id
                ) choices
            ), '[]'::jsonb) AS genre_sets,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'value', key_signature_set.id,
                        'label', key_signature_set.signatures::text
                    ) ORDER BY key_signature_set.id
                )
                FROM key_signature_set
            ), '[]'::jsonb) AS key_signature_sets,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'value', time_signature_set.id,
                        'label', time_signature_set.signatures::text
                    ) ORDER BY time_signature_set.id
                )
                FROM time_signature_set
            ), '[]'::jsonb) AS time_signature_sets,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object('value', language.id, 'label', language.name)
                    ORDER BY language.name, language.id
                )
                FROM language
            ), '[]'::jsonb) AS languages,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object('value', status.name, 'label', status.name)
                    ORDER BY status.name
                )
                FROM song_approval_status status
            ), '[]'::jsonb) AS approval_statuses
        """
    )
    choices = cursor.fetchone()
    config["country_id"].update(identity=True, choices=choices["countries"])
    config["year_id"].update(identity=True, choices=choices["years"])
    config["submitter_id"]["choices"] = choices["accounts"]
    config["language_set_id"]["choices"] = choices["language_sets"]
    config["genre_set_id"]["choices"] = choices["genre_sets"]
    config["key_signature_set_id"]["choices"] = choices["key_signature_sets"]
    config["time_signature_set_id"]["choices"] = choices["time_signature_sets"]
    config["title_language_id"]["choices"] = choices["languages"]
    config["native_language_id"]["choices"] = choices["languages"]
    config["approval_status"]["choices"] = choices["approval_statuses"]
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
            if field in IDENTITY_FIELDS and boundary == "from":
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
        if field in IDENTITY_FIELDS:
            identity_column = {
                "country_id": "raw.song_country_id",
                "year_id": "raw.song_year_id",
            }[field]
            clause = f"{identity_column} IS NOT NULL"
            clause_params = []
            if "to" in selected_filter:
                value_clause = f"{identity_column} = %s"
                if selected_filter.get("not"):
                    value_clause = f"NOT ({value_clause})"
                clause = f"({clause}) AND ({value_clause})"
                clause_params.append(selected_filter["to"])
            elif selected_filter.get("not"):
                clause = f"NOT ({clause})"

            if index:
                clauses.append(selected_filter.get("join", "and").upper())
            clauses.append(f"({clause})")
            params.extend(clause_params)
            continue

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

    collection_labels = {
        "genre_set_id": "Genres",
        "key_signature_set_id": "Key signatures",
        "time_signature_set_id": "Time signatures",
    }
    for field, label in collection_labels.items():
        if field in changed_fields:
            details.append(label)

    elaborated_fields = {
        "title", "artist", "submitter_id", "approval_status", "is_placeholder",
        *collection_labels,
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


def _execute_unfiltered_changes_query(
    cursor, before_at, from_time_at, to_time_at, per_page
):
    """Load a page after paginating narrow audit identifiers.

    Expanding ``song_change`` computes JSON diffs for a revision and its
    predecessor. Keep that work out of the full-history scan used to locate the
    current page.
    """
    cursor.execute(
        """
        WITH candidate_changes AS MATERIALIZED (
            SELECT 'd'::text AS source, data.id, data.created_at AS changed_at
            FROM song_data AS data
            WHERE (%s::timestamptz IS NULL OR data.created_at < %s::timestamptz)
              AND (%s::timestamptz IS NULL OR data.created_at >= %s::timestamptz)
              AND (%s::timestamptz IS NULL OR data.created_at <= %s::timestamptz)

            UNION ALL

            SELECT 's'::text AS source, status.id, status.created_at AS changed_at
            FROM song_status AS status
            JOIN LATERAL (
                SELECT older.approval_status, older.is_placeholder
                FROM song_status AS older
                WHERE older.song_id = status.song_id
                  AND (older.created_at, older.id)
                      < (status.created_at, status.id)
                ORDER BY older.created_at DESC, older.id DESC
                LIMIT 1
            ) AS previous ON true
            WHERE status.changed_by IS NOT NULL
              AND (
                  status.approval_status IS DISTINCT FROM previous.approval_status
                  OR status.is_placeholder IS DISTINCT FROM previous.is_placeholder
              )
              AND (%s::timestamptz IS NULL OR status.created_at < %s::timestamptz)
              AND (%s::timestamptz IS NULL OR status.created_at >= %s::timestamptz)
              AND (%s::timestamptz IS NULL OR status.created_at <= %s::timestamptz)
        ), page_candidates AS MATERIALIZED (
            SELECT candidate.*
            FROM candidate_changes AS candidate
            ORDER BY candidate.changed_at DESC
            FETCH FIRST %s ROWS WITH TIES
        ), page_boundary AS (
            SELECT MIN(changed_at) AS changed_at
            FROM page_candidates
        ), paging AS (
            SELECT EXISTS (
                SELECT 1
                FROM candidate_changes AS older
                CROSS JOIN page_boundary AS boundary
                WHERE older.changed_at < boundary.changed_at
            ) AS has_older
        ), artist_names AS MATERIALIZED (
            SELECT credit.artist_credit_set_id,
                   STRING_AGG(
                       COALESCE(credit.join_phrase, '')
                       || COALESCE(credit.stage_name, artist.full_name),
                       '' ORDER BY credit.position
                   ) AS name
            FROM artist_credit AS credit
            JOIN artist ON artist.id = credit.artist_id
            GROUP BY credit.artist_credit_set_id
        ), page_changes AS MATERIALIZED (
            SELECT
                candidate.source,
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
                    WHEN change.event_type = 'create'
                        THEN ARRAY['creation']::text[]
                    WHEN change.event_type = 'delete'
                        THEN ARRAY['deletion']::text[]
                    WHEN change.event_type = 'song_replacement'
                        THEN ARRAY['replacement']::text[]
                    ELSE ARRAY['modification']::text[]
                END AS event_categories
            FROM page_candidates AS candidate
            JOIN song_change AS change ON change.id = candidate.id
            WHERE candidate.source = 'd'

            UNION ALL

            SELECT
                candidate.source,
                1 AS source_order,
                status.id,
                status.created_at AS changed_at,
                data.song_id,
                data.title AS song_title,
                artist_names.name AS song_artist,
                data.country_id AS song_country_id,
                data.year_id AS song_year_id,
                JSONB_STRIP_NULLS(JSONB_BUILD_OBJECT(
                    'approval_status', CASE
                        WHEN status.approval_status
                             IS DISTINCT FROM previous.approval_status
                        THEN JSONB_BUILD_OBJECT(
                            'old', previous.approval_status,
                            'new', status.approval_status
                        ) END,
                    'is_placeholder', CASE
                        WHEN status.is_placeholder
                             IS DISTINCT FROM previous.is_placeholder
                        THEN JSONB_BUILD_OBJECT(
                            'old', previous.is_placeholder,
                            'new', status.is_placeholder
                        ) END
                )) AS changed_fields,
                status.changed_by,
                ARRAY_REMOVE(ARRAY[
                    CASE WHEN status.approval_status
                                   IS DISTINCT FROM previous.approval_status
                         THEN 'status_change' END,
                    CASE WHEN status.is_placeholder
                                   IS DISTINCT FROM previous.is_placeholder
                         THEN 'placeholder' END
                ], NULL) AS event_categories
            FROM page_candidates AS candidate
            JOIN song_status AS status ON status.id = candidate.id
            JOIN LATERAL (
                SELECT older.approval_status, older.is_placeholder
                FROM song_status AS older
                WHERE older.song_id = status.song_id
                  AND (older.created_at, older.id)
                      < (status.created_at, status.id)
                ORDER BY older.created_at DESC, older.id DESC
                LIMIT 1
            ) AS previous ON true
            JOIN song_data AS data ON data.id = status.song_data_id
            LEFT JOIN artist_names
              ON artist_names.artist_credit_set_id = data.artist_credit_set_id
            WHERE candidate.source = 's'
        )
        SELECT page.*, account.username AS changed_by_username,
               country.name AS country_name,
               year.special_name AS song_special_name,
               year.special_short_name AS song_special_short_name,
               paging.has_older
        FROM page_changes AS page
        CROSS JOIN paging
        LEFT JOIN account ON account.id = page.changed_by
        LEFT JOIN country ON country.id = page.song_country_id
        LEFT JOIN year ON year.id = page.song_year_id
        ORDER BY page.changed_at DESC, page.id DESC, page.source_order DESC
        """,
        (
            before_at,
            before_at,
            from_time_at,
            from_time_at,
            to_time_at,
            to_time_at,
            before_at,
            before_at,
            from_time_at,
            from_time_at,
            to_time_at,
            to_time_at,
            per_page,
        ),
    )


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

    if set(selected_categories) == set(EVENT_CATEGORIES) and not selected_filters:
        _execute_unfiltered_changes_query(
            cursor, before_at, from_time_at, to_time_at, per_page
        )
    else:
        cursor.execute(
            """
        WITH artist_names AS MATERIALIZED (
            SELECT credit.artist_credit_set_id,
                   STRING_AGG(
                       COALESCE(credit.join_phrase, '')
                       || COALESCE(credit.stage_name, artist.full_name),
                       '' ORDER BY credit.position
                   ) AS name
            FROM artist_credit AS credit
            JOIN artist ON artist.id = credit.artist_id
            GROUP BY credit.artist_credit_set_id
        ), raw_changes AS NOT MATERIALIZED (
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
                artist_names.name AS song_artist,
                data.country_id AS song_country_id,
                data.year_id AS song_year_id,
                JSONB_STRIP_NULLS(JSONB_BUILD_OBJECT(
                    'approval_status', CASE
                        WHEN status.approval_status
                             IS DISTINCT FROM previous.approval_status
                        THEN JSONB_BUILD_OBJECT(
                            'old', previous.approval_status,
                            'new', status.approval_status
                        ) END,
                    'is_placeholder', CASE
                        WHEN status.is_placeholder
                             IS DISTINCT FROM previous.is_placeholder
                        THEN JSONB_BUILD_OBJECT(
                            'old', previous.is_placeholder,
                            'new', status.is_placeholder
                        ) END
                )) AS changed_fields,
                status.changed_by,
                ARRAY_REMOVE(ARRAY[
                    CASE WHEN status.approval_status
                                   IS DISTINCT FROM previous.approval_status
                         THEN 'status_change' END,
                    CASE WHEN status.is_placeholder
                                   IS DISTINCT FROM previous.is_placeholder
                         THEN 'placeholder' END
                ], NULL) AS event_categories
            FROM song_status AS status
            JOIN LATERAL (
                SELECT older.approval_status, older.is_placeholder
                FROM song_status older
                WHERE older.song_id = status.song_id
                  AND (older.created_at, older.id)
                      < (status.created_at, status.id)
                ORDER BY older.created_at DESC, older.id DESC
                LIMIT 1
            ) previous ON true
            JOIN song_data AS data ON data.id = status.song_data_id
            LEFT JOIN artist_names
              ON artist_names.artist_credit_set_id = data.artist_credit_set_id
            WHERE status.changed_by IS NOT NULL
              AND (
                  status.approval_status IS DISTINCT FROM previous.approval_status
                  OR status.is_placeholder IS DISTINCT FROM previous.is_placeholder
              )
        ), filtered_changes AS NOT MATERIALIZED (
            SELECT raw.*
            FROM raw_changes AS raw
            WHERE raw.event_categories && %s::text[]
              AND (%s::timestamptz IS NULL OR raw.changed_at < %s::timestamptz)
              AND (%s::timestamptz IS NULL OR raw.changed_at >= %s::timestamptz)
              AND (%s::timestamptz IS NULL OR raw.changed_at <= %s::timestamptz)
              AND ("""
            + filter_expression
            + """)
        ), page_changes AS MATERIALIZED (
            SELECT filtered_changes.*
            FROM filtered_changes
            ORDER BY changed_at DESC
            FETCH FIRST %s ROWS WITH TIES
        ), page_boundary AS (
            SELECT MIN(changed_at) AS changed_at
            FROM page_changes
        ), paging AS (
            SELECT EXISTS (
                SELECT 1
                FROM filtered_changes older
                CROSS JOIN page_boundary boundary
                WHERE older.changed_at < boundary.changed_at
            ) AS has_older
        )
        SELECT page.*, a.username AS changed_by_username,
               c.name AS country_name,
               y.special_name AS song_special_name,
               y.special_short_name AS song_special_short_name,
               paging.has_older
        FROM page_changes AS page
        CROSS JOIN paging
        LEFT JOIN account a ON a.id = page.changed_by
        LEFT JOIN country c ON c.id = page.song_country_id
        LEFT JOIN year y ON y.id = page.song_year_id
        ORDER BY page.changed_at DESC, page.id DESC, page.source_order DESC
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
            ),
        )
    audit_changes = cursor.fetchall()
    has_older = bool(audit_changes) and audit_changes[0]["has_older"]
    next_before = audit_changes[-1]["changed_at"].isoformat() if has_older else None

    filter_fields = _filter_field_config(cursor)
    username_map = {}
    for choice in filter_fields["submitter_id"]["choices"]:
        username_map[choice["value"]] = choice["label"]
        username_map[str(choice["value"])] = choice["label"]

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
        filter_fields=filter_fields,
        selected_events=selected_categories,
        selected_filters=selected_filters,
        serialized_filters=serialized_filters,
    )
