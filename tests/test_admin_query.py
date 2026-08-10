import datetime

import pytest

from world_stage.admin_query import (
    QueryDefinitionError,
    SchemaObject,
    bind_named_sql,
    coerce_parameter_value,
    compile_builder_query,
    detect_sql_operation,
    validate_raw_sql,
)

CATALOG = {
    "song": SchemaObject("song", "table", frozenset({"id", "submitter_id", "title", "year_id"})),
    "account": SchemaObject("account", "table", frozenset({"id", "username"})),
    "current_song": SchemaObject(
        "current_song", "view", frozenset({"id", "submitter_id", "title", "year_id"})
    ),
}


def test_compile_grouped_select_with_join_having_and_pagination():
    definition = {
        "operation": "select",
        "from": {"table": "song", "alias": "song"},
        "joins": [
            {
                "type": "left",
                "table": "account",
                "alias": "submitter",
                "on": [
                    {
                        "left": {"table": "song", "column": "submitter_id"},
                        "right": {"table": "submitter", "column": "id"},
                    }
                ],
            }
        ],
        "columns": [
            {"kind": "column", "table": "submitter", "column": "username"},
            {
                "kind": "aggregate",
                "function": "count",
                "column": "*",
                "alias": "song_count",
            },
        ],
        "where": {
            "left": {"kind": "column", "table": "song", "column": "year_id"},
            "operator": "eq",
            "value": {"kind": "parameter", "name": "year"},
        },
        "group_by": [{"kind": "column", "table": "submitter", "column": "username"}],
        "having": {
            "left": {"kind": "output", "name": "song_count"},
            "operator": "gte",
            "value": {"kind": "literal", "value": 2},
        },
        "order_by": [
            {
                "expression": {"kind": "output", "name": "song_count"},
                "direction": "desc",
            }
        ],
        "limit": {"kind": "literal", "value": 25},
        "offset": {"kind": "literal", "value": 10},
    }

    compiled = compile_builder_query(
        definition,
        CATALOG,
        {"year": "2025"},
        [{"name": "year", "type": "integer", "required": True}],
    )

    assert compiled.statement.as_string(None) == (
        'SELECT "submitter"."username", COUNT(*) AS "song_count" '
        'FROM "song" AS "song" LEFT JOIN "account" AS "submitter" '
        'ON "song"."submitter_id" = "submitter"."id" '
        'WHERE "song"."year_id" = %s GROUP BY "submitter"."username" '
        "HAVING COUNT(*) >= %s ORDER BY COUNT(*) DESC LIMIT %s OFFSET %s"
    )
    assert compiled.params == (2025, 2, 25, 10)
    assert compiled.referenced_objects == ("account", "song")


def test_update_requires_a_filter_and_views_are_read_only():
    base = {
        "operation": "update",
        "from": {"table": "song", "alias": "song"},
        "values": [{"column": "title", "value": {"kind": "literal", "value": "Fixed"}}],
    }
    with pytest.raises(QueryDefinitionError, match="requires at least one filter"):
        compile_builder_query(base, CATALOG)

    base["from"] = {"table": "current_song", "alias": "song"}
    with pytest.raises(QueryDefinitionError, match="table, not a view"):
        compile_builder_query(base, CATALOG)


def test_having_can_use_an_aggregate_not_present_in_results():
    definition = {
        "operation": "select",
        "from": {"table": "song", "alias": "song"},
        "columns": [
            {"kind": "column", "table": "song", "column": "submitter_id"},
            {
                "kind": "aggregate",
                "function": "count",
                "column": "*",
                "alias": "song_count",
            },
        ],
        "group_by": [{"kind": "column", "table": "song", "column": "submitter_id"}],
        "having": {
            "left": {
                "kind": "aggregate",
                "function": "avg",
                "table": "song",
                "column": "year_id",
            },
            "operator": "gte",
            "value": {"kind": "literal", "value": 2020},
        },
    }

    compiled = compile_builder_query(definition, CATALOG)

    assert 'HAVING AVG("song"."year_id") >= %s' in compiled.statement.as_string(None)
    assert compiled.params == (2020,)


def test_named_parameters_ignore_literals_comments_casts_and_dollar_quotes():
    query = """SELECT '@hidden', @user_id::bigint, $$@also_hidden$$
               -- @commented
               WHERE username = @name /* @ignored */"""
    rendered, params = bind_named_sql(
        query,
        {"user_id": "12", "name": "alice"},
        [
            {"name": "user_id", "type": "integer"},
            {"name": "name", "type": "text"},
        ],
    )

    assert "%s::bigint" in rendered
    assert "username = %s" in rendered
    assert params == (12, "alice")


@pytest.mark.parametrize(
    ("query", "operation"),
    [
        ("SELECT 1", "select"),
        ("WITH source AS (SELECT 1) UPDATE song SET title = 'x'", "update"),
        ("WITH deleted AS (DELETE FROM song RETURNING *) SELECT * FROM deleted", "select"),
        ("DELETE FROM song", "delete"),
    ],
)
def test_detect_sql_operation(query, operation):
    assert detect_sql_operation(query) == operation


def test_raw_sql_accepts_deletes_but_rejects_multiple_statements():
    assert validate_raw_sql("DELETE FROM song") == "delete"
    assert (
        validate_raw_sql("WITH deleted AS (DELETE FROM song RETURNING *) SELECT * FROM deleted")
        == "delete"
    )
    with pytest.raises(QueryDefinitionError, match="exactly one"):
        validate_raw_sql("SELECT 1; UPDATE song SET title = 'nope'")
    assert validate_raw_sql("SELECT ';' AS value; -- one trailing terminator") == "select"


def test_parameter_coercion():
    assert coerce_parameter_value("false", "boolean") is False
    assert coerce_parameter_value("2026-08-10", "date") == datetime.date(2026, 8, 10)
    assert coerce_parameter_value(["1", 2], "integer[]") == [1, 2]
