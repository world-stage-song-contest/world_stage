from __future__ import annotations

import csv
import datetime
import io
import json
import os
import re
import subprocess
import time
from numbers import Number
from typing import Any

import psycopg
from flask import Response, current_app, jsonify, request
from psycopg import sql

from ...admin_query import (
    QueryDefinitionError,
    bind_named_sql,
    compile_builder_query,
    json_safe_rows,
    load_schema_catalog,
    query_fingerprint,
    validate_raw_sql,
)
from ...db import get_db
from ...utils import get_session_auth
from .common import bp

PARAMETER_TYPES = {
    "text",
    "integer",
    "bigint",
    "number",
    "numeric",
    "boolean",
    "date",
    "datetime",
    "timestamp",
    "text[]",
    "integer[]",
}

MAX_SAVED_QUERY_TAGS = 20
MAX_SAVED_QUERY_TAG_LENGTH = 50

FRIENDLY_LABELS = {
    "account": "username",
    "country": "name",
    "language": "name",
    "genre": "name",
    "subgenre": "name",
    "national_final": "name",
    "show": "show_name",
    "show_types": "name",
}

VIRTUAL_RELATIONSHIPS = [
    ("current_song", "id", "song", "id"),
    ("current_song", "submitter_id", "account", "id"),
    ("current_song", "country_id", "country", "id"),
    ("current_song", "year_id", "year", "id"),
    ("current_song", "language_set_id", "language_set", "id"),
    ("current_song", "genre_set_id", "genre_set", "id"),
]


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _actor_id() -> int | None:
    user, _permissions = get_session_auth(request.cookies.get("session"))
    return user[0] if user else None


def _parameter_schema(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise QueryDefinitionError("Parameters must be an array")
    result = []
    names = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise QueryDefinitionError("Every parameter must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise QueryDefinitionError(f"Invalid parameter name: {name!r}")
        if name in names:
            raise QueryDefinitionError(f"Duplicate parameter: {name}")
        names.add(name)
        parameter_type = str(raw.get("type", "text"))
        if parameter_type not in PARAMETER_TYPES:
            raise QueryDefinitionError(f"Unsupported parameter type: {parameter_type}")
        item = {
            "name": name,
            "label": str(raw.get("label") or name.replace("_", " ").title())[:200],
            "type": parameter_type,
            "required": bool(raw.get("required", True)),
        }
        if "default" in raw:
            item["default"] = raw["default"]
        picker = raw.get("picker")
        if isinstance(picker, dict):
            item["picker"] = {
                "table": str(picker.get("table", "")),
                "column": str(picker.get("column", "")),
            }
        result.append(item)
    return result


def _saved_query_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise QueryDefinitionError("Tags must be an array")
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        if not isinstance(raw_tag, str):
            raise QueryDefinitionError("Every tag must be text")
        tag = raw_tag.strip()
        if not tag:
            continue
        if len(tag) > MAX_SAVED_QUERY_TAG_LENGTH:
            raise QueryDefinitionError(
                f"Tags cannot be longer than {MAX_SAVED_QUERY_TAG_LENGTH} characters"
            )
        normalized = tag.casefold()
        if normalized not in seen:
            seen.add(normalized)
            tags.append(tag)
    if len(tags) > MAX_SAVED_QUERY_TAGS:
        raise QueryDefinitionError(f"A query can have at most {MAX_SAVED_QUERY_TAGS} tags")
    return tags


def _saved_query_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "query_kind": row["query_kind"],
        "operation": row["operation"],
        "definition": row["definition"],
        "sql_text": row["sql_text"],
        "parameters": row["parameters"],
        "tags": row["tags"],
        "created_by": row.get("created_by"),
        "created_by_username": row.get("created_by_username"),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@bp.get("/fuckupdb/api/schema")
def database_schema():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT columns.table_name, columns.column_name, columns.ordinal_position,
               columns.data_type, columns.udt_name, columns.is_nullable = 'YES' AS nullable,
               columns.column_default,
               EXISTS (
                   SELECT 1
                   FROM information_schema.table_constraints AS constraints
                   JOIN information_schema.key_column_usage AS key_columns
                     ON key_columns.constraint_schema = constraints.constraint_schema
                    AND key_columns.constraint_name = constraints.constraint_name
                   WHERE constraints.table_schema = columns.table_schema
                     AND constraints.table_name = columns.table_name
                     AND constraints.constraint_type = 'PRIMARY KEY'
                     AND key_columns.column_name = columns.column_name
               ) AS primary_key
        FROM information_schema.columns AS columns
        WHERE columns.table_schema = 'public'
          AND LEFT(columns.table_name, 3) <> 'pg_'
        ORDER BY columns.table_name, columns.ordinal_position
        """
    )
    columns_by_object: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        columns_by_object.setdefault(row["table_name"], []).append(
            {
                "name": row["column_name"],
                "type": row["data_type"] if row["data_type"] != "USER-DEFINED" else row["udt_name"],
                "nullable": row["nullable"],
                "default": row["column_default"],
                "primary_key": bool(row["primary_key"]),
            }
        )

    cursor.execute(
        """
        SELECT source.relname AS source_table, source_attribute.attname AS source_column,
               target.relname AS target_table, target_attribute.attname AS target_column,
               constraint_row.conname AS constraint_name
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS source ON source.oid = constraint_row.conrelid
        JOIN pg_namespace AS source_namespace ON source_namespace.oid = source.relnamespace
        JOIN pg_class AS target ON target.oid = constraint_row.confrelid
        JOIN pg_namespace AS target_namespace ON target_namespace.oid = target.relnamespace
        JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
             AS source_key(attnum, position) ON true
        JOIN LATERAL unnest(constraint_row.confkey) WITH ORDINALITY
             AS target_key(attnum, position) ON target_key.position = source_key.position
        JOIN pg_attribute AS source_attribute
          ON source_attribute.attrelid = source.oid
         AND source_attribute.attnum = source_key.attnum
        JOIN pg_attribute AS target_attribute
          ON target_attribute.attrelid = target.oid
         AND target_attribute.attnum = target_key.attnum
        WHERE constraint_row.contype = 'f'
          AND source_namespace.nspname = 'public'
          AND target_namespace.nspname = 'public'
          AND LEFT(source.relname, 3) <> 'pg_'
          AND LEFT(target.relname, 3) <> 'pg_'
        ORDER BY source.relname, constraint_row.conname, source_key.position
        """
    )
    relationships = [dict(row) for row in cursor.fetchall()]
    existing = {
        (item["source_table"], item["source_column"], item["target_table"], item["target_column"])
        for item in relationships
    }
    for source_table, source_column, target_table, target_column in VIRTUAL_RELATIONSHIPS:
        key = (source_table, source_column, target_table, target_column)
        if (
            key not in existing
            and source_table in columns_by_object
            and target_table in columns_by_object
        ):
            relationships.append(
                {
                    "source_table": source_table,
                    "source_column": source_column,
                    "target_table": target_table,
                    "target_column": target_column,
                    "constraint_name": None,
                    "virtual": True,
                }
            )
    references = {
        (item["source_table"], item["source_column"]): {
            "table": item["target_table"],
            "column": item["target_column"],
        }
        for item in relationships
    }
    cursor.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND LEFT(table_name, 3) <> 'pg_'
        UNION ALL
        SELECT table_name, 'VIEW'
        FROM information_schema.views
        WHERE table_schema = 'public'
          AND LEFT(table_name, 3) <> 'pg_'
          AND table_name NOT IN (
              SELECT table_name FROM information_schema.tables
              WHERE table_schema = 'public'
                AND LEFT(table_name, 3) <> 'pg_'
          )
        ORDER BY table_name
        """
    )
    objects = []
    for row in cursor.fetchall():
        name = row["table_name"]
        object_columns = columns_by_object.get(name, [])
        for column in object_columns:
            reference = references.get((name, column["name"]))
            if reference:
                column["references"] = reference
                column["picker"] = {
                    **reference,
                    "label_column": FRIENDLY_LABELS.get(reference["table"], reference["column"]),
                }
        objects.append(
            {
                "name": name,
                "kind": "view" if "VIEW" in row["table_type"] else "table",
                "columns": object_columns,
            }
        )
    return jsonify({"objects": objects, "relationships": relationships})


@bp.get("/fuckupdb/api/options")
def database_options():
    table = request.args.get("table", "")
    column = request.args.get("column", "")
    search = request.args.get("q", "")[:100]
    db = get_db()
    cursor = db.cursor()
    catalog = load_schema_catalog(cursor)
    if table not in catalog or column not in catalog[table].columns:
        return _json_error("Unknown option source")
    label_column = FRIENDLY_LABELS.get(table, column)
    if label_column not in catalog[table].columns:
        label_column = column
    cursor.execute(
        sql.SQL(
            "SELECT {}::text AS value, {}::text AS label "
            "FROM {} WHERE COALESCE({}::text, '') ILIKE %s OR {}::text ILIKE %s "
            "ORDER BY {} NULLS LAST, {} LIMIT 50"
        ).format(
            sql.Identifier(column),
            sql.Identifier(label_column),
            sql.Identifier(table),
            sql.Identifier(label_column),
            sql.Identifier(column),
            sql.Identifier(label_column),
            sql.Identifier(column),
        ),
        (f"%{search}%", f"%{search}%"),
    )
    return jsonify({"options": cursor.fetchall()})


@bp.get("/fuckupdb/api/saved-queries")
def saved_queries():
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT saved.*, account.username AS created_by_username
        FROM admin_saved_query AS saved
        LEFT JOIN account ON account.id = saved.created_by
        ORDER BY LOWER(saved.name), saved.id
        """
    )
    return jsonify({"queries": [_saved_query_row(row) for row in cursor.fetchall()]})


def _save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name or len(name) > 200:
        raise QueryDefinitionError("A query name between 1 and 200 characters is required")
    query_kind = str(payload.get("query_kind", "builder"))
    if query_kind not in {"builder", "sql"}:
        raise QueryDefinitionError("Unknown saved query type")
    definition = payload.get("definition") if query_kind == "builder" else None
    sql_text = str(payload.get("sql_text", "")).strip() if query_kind == "sql" else None
    if query_kind == "builder" and not isinstance(definition, dict):
        raise QueryDefinitionError("A builder definition is required")
    if query_kind == "sql" and not sql_text:
        raise QueryDefinitionError("SQL text is required")
    operation = (
        str(definition.get("operation", "select")).lower()
        if definition is not None
        else validate_raw_sql(sql_text or "")
    )
    allowed_operations = (
        {"select", "insert", "update"}
        if query_kind == "builder"
        else {"select", "insert", "update", "delete"}
    )
    if operation not in allowed_operations:
        if query_kind == "builder":
            raise QueryDefinitionError("The builder supports SELECT, INSERT, and UPDATE")
        raise QueryDefinitionError("Stored SQL supports SELECT, INSERT, UPDATE, and DELETE")
    return {
        "name": name,
        "description": str(payload.get("description", ""))[:2000],
        "query_kind": query_kind,
        "operation": operation,
        "definition": definition,
        "sql_text": sql_text,
        "parameters": _parameter_schema(payload.get("parameters")),
        "tags": _saved_query_tags(payload.get("tags")),
    }


@bp.post("/fuckupdb/api/saved-queries")
def create_saved_query():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("A JSON request body is required")
    try:
        values = _save_payload(payload)
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO admin_saved_query
                (name, description, query_kind, operation, definition, sql_text,
                 parameters, tags, created_by)
            VALUES (%(name)s, %(description)s, %(query_kind)s, %(operation)s,
                    %(definition)s::jsonb, %(sql_text)s, %(parameters)s::jsonb,
                    %(tags)s, %(created_by)s)
            RETURNING *
            """,
            {
                **values,
                "definition": (json.dumps(values["definition"]) if values["definition"] else None),
                "parameters": json.dumps(values["parameters"]),
                "created_by": _actor_id(),
            },
        )
        row = cursor.fetchone()
        assert row is not None
        cursor.execute("SELECT username FROM account WHERE id = %s", (row["created_by"],))
        creator = cursor.fetchone()
        row["created_by_username"] = creator["username"] if creator else None
        db.commit()
        return jsonify({"query": _saved_query_row(row)}), 201
    except QueryDefinitionError as exc:
        return _json_error(str(exc))
    except psycopg.errors.UniqueViolation:
        get_db().rollback()
        return _json_error("A saved query with that name already exists", 409)


@bp.put("/fuckupdb/api/saved-queries/<int:query_id>")
def update_saved_query(query_id: int):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("A JSON request body is required")
    try:
        values = _save_payload(payload)
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            UPDATE admin_saved_query
            SET name = %(name)s, description = %(description)s,
                query_kind = %(query_kind)s, operation = %(operation)s,
                definition = %(definition)s::jsonb, sql_text = %(sql_text)s,
                parameters = %(parameters)s::jsonb, tags = %(tags)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
            RETURNING *
            """,
            {
                **values,
                "definition": (json.dumps(values["definition"]) if values["definition"] else None),
                "parameters": json.dumps(values["parameters"]),
                "id": query_id,
            },
        )
        row = cursor.fetchone()
        if row is None:
            db.rollback()
            return _json_error("Saved query not found", 404)
        cursor.execute("SELECT username FROM account WHERE id = %s", (row["created_by"],))
        creator = cursor.fetchone()
        row["created_by_username"] = creator["username"] if creator else None
        db.commit()
        return jsonify({"query": _saved_query_row(row)})
    except QueryDefinitionError as exc:
        return _json_error(str(exc))
    except psycopg.errors.UniqueViolation:
        get_db().rollback()
        return _json_error("A saved query with that name already exists", 409)


@bp.delete("/fuckupdb/api/saved-queries/<int:query_id>")
def delete_saved_query(query_id: int):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM admin_saved_query WHERE id = %s RETURNING id", (query_id,))
    if cursor.fetchone() is None:
        db.rollback()
        return _json_error("Saved query not found", 404)
    db.commit()
    return Response(status=204)


def _insert_log(
    *,
    actor_id: int | None,
    saved_query_id: int | None,
    source: str,
    operation: str,
    query_text: str,
    definition: dict[str, Any] | None,
    parameters: list[dict[str, Any]],
    referenced_objects: list[str],
    result_format: str,
) -> int:
    db = get_db()
    cursor = db.cursor()
    fingerprint_source: str | dict[str, Any] = definition if definition is not None else query_text
    cursor.execute(
        """
        INSERT INTO admin_query_log
            (actor_id, saved_query_id, source, operation, query_text, definition,
             parameter_schema, referenced_objects, result_format, query_fingerprint)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
        RETURNING id
        """,
        (
            actor_id,
            saved_query_id,
            source,
            operation if operation in {"select", "insert", "update", "delete"} else "other",
            query_text,
            json.dumps(definition) if definition is not None else None,
            json.dumps(parameters),
            referenced_objects,
            result_format,
            query_fingerprint(fingerprint_source),
        ),
    )
    row = cursor.fetchone()
    assert row is not None
    db.commit()
    return row["id"]


def _finish_log(
    log_id: int,
    *,
    success: bool,
    row_count: int | None,
    duration_ms: int,
    error: psycopg.Error | None = None,
):
    db = get_db()
    db.execute(
        """
        UPDATE admin_query_log
        SET success = %s, row_count = %s, duration_ms = %s,
            sqlstate = %s, error_message = %s, completed_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (
            success,
            row_count,
            duration_ms,
            error.sqlstate if error else None,
            str(error)[:1000] if error else None,
            log_id,
        ),
    )
    db.commit()


@bp.post("/fuckupdb/api/execute")
def execute_database_query():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("A JSON request body is required")
    db = get_db()
    cursor = db.cursor()
    saved_query_id = payload.get("saved_query_id")
    saved = None
    if saved_query_id is not None:
        cursor.execute("SELECT * FROM admin_saved_query WHERE id = %s", (saved_query_id,))
        saved = cursor.fetchone()
        if saved is None:
            return _json_error("Saved query not found", 404)
    try:
        if saved is not None:
            kind = saved["query_kind"]
            definition = saved["definition"]
            raw_sql = saved["sql_text"]
            parameters = _parameter_schema(saved["parameters"])
            source = f"stored_{kind}"
        else:
            kind = str(payload.get("query_kind", "builder"))
            definition = payload.get("definition")
            raw_sql = payload.get("sql_text")
            parameters = _parameter_schema(payload.get("parameters"))
            source = kind
        values = payload.get("values") or {}
        if not isinstance(values, dict):
            raise QueryDefinitionError("Parameter values must be an object")
        result_format = str(payload.get("result_format", "screen"))
        if result_format not in {"screen", "csv"}:
            raise QueryDefinitionError("Unknown result format")
        if kind == "builder":
            if not isinstance(definition, dict):
                raise QueryDefinitionError("A builder definition is required")
            compiled = compile_builder_query(
                definition, load_schema_catalog(cursor), values, parameters
            )
            statement: Any = compiled.statement
            bound_params = compiled.params
            operation = compiled.operation
            referenced_objects = list(compiled.referenced_objects)
            query_text = statement.as_string(db)
        elif kind == "sql":
            if not isinstance(raw_sql, str) or not raw_sql.strip():
                raise QueryDefinitionError("SQL text is required")
            query_text, bound_params = bind_named_sql(raw_sql, values, parameters)
            statement = query_text
            operation = validate_raw_sql(query_text)
            referenced_objects = []
            definition = None
        else:
            raise QueryDefinitionError("Unknown query type")
    except QueryDefinitionError as exc:
        return _json_error(str(exc))

    log_id = _insert_log(
        actor_id=_actor_id(),
        saved_query_id=saved_query_id,
        source=source,
        operation=operation,
        query_text=query_text,
        definition=definition,
        parameters=parameters,
        referenced_objects=referenced_objects,
        result_format=result_format,
    )
    backup_script = current_app.config.get("BACKUP_SCRIPT", os.environ.get("BACKUP_SCRIPT", ""))
    if backup_script:
        subprocess.run(backup_script, check=False)
    started = time.perf_counter()
    try:
        cursor = db.cursor()
        cursor.execute("SET LOCAL ROLE dml_only_role")
        cursor.execute(statement, bound_params)
        headers = (
            [description.name for description in cursor.description] if cursor.description else []
        )
        rows = cursor.fetchall() if cursor.description else []
        row_count = len(rows) if cursor.description else max(cursor.rowcount, 0)
        db.commit()
        duration_ms = round((time.perf_counter() - started) * 1000)
        _finish_log(log_id, success=True, row_count=row_count, duration_ms=duration_ms)
    except psycopg.Error as exc:
        db.rollback()
        duration_ms = round((time.perf_counter() - started) * 1000)
        _finish_log(log_id, success=False, row_count=None, duration_ms=duration_ms, error=exc)
        return _json_error(f"Query failed: {exc}")

    safe_rows = json_safe_rows(rows)
    numeric_headers = [
        header
        for header in headers
        if any(
            isinstance(row.get(header), Number) and not isinstance(row.get(header), bool)
            for row in rows
        )
    ]
    if result_format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(safe_rows)
        filename = datetime.datetime.now(tz=datetime.UTC).strftime("query_%Y%m%dT%H%M%SZ.csv")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return jsonify(
        {
            "headers": headers,
            "numeric_headers": numeric_headers,
            "rows": safe_rows,
            "row_count": row_count,
            "duration_ms": duration_ms,
            "log_id": log_id,
        }
    )


@bp.get("/fuckupdb/api/query-logs")
def database_query_logs():
    _user, permissions = get_session_auth(request.cookies.get("session"))
    if permissions.role != "owner":
        return _json_error("Only owners can access query logs", 403)
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT log.*, account.username AS actor_username, saved.name AS saved_query_name
        FROM admin_query_log AS log
        LEFT JOIN account ON account.id = log.actor_id
        LEFT JOIN admin_saved_query AS saved ON saved.id = log.saved_query_id
        ORDER BY log.created_at DESC, log.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return jsonify({"logs": json_safe_rows(cursor.fetchall())})
