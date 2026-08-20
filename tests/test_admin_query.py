import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

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

SQL_OPERATIONS = {
    "select": "SELECT 1",
    "insert": "INSERT INTO song (id) VALUES (1)",
    "update": "UPDATE song SET title = 'changed'",
    "delete": "DELETE FROM song",
}


@given(
    aggregate=st.sampled_from(["count", "sum", "avg", "min", "max"]),
    year=st.integers(min_value=1900, max_value=3000),
    threshold=st.integers(),
    limit=st.integers(min_value=0, max_value=10_000),
    offset=st.integers(min_value=0, max_value=10_000),
)
def test_builder_reports_semantics_for_generated_grouped_queries(
    aggregate, year, threshold, limit, offset
):
    aggregate_column = "*" if aggregate == "count" else "year_id"
    definition = {
        "operation": "select",
        "from": {"table": "song", "alias": "entry"},
        "joins": [
            {
                "type": "left",
                "table": "account",
                "alias": "owner",
                "on": [
                    {
                        "left": {"table": "entry", "column": "submitter_id"},
                        "right": {"table": "owner", "column": "id"},
                    }
                ],
            }
        ],
        "columns": [
            {"kind": "column", "table": "owner", "column": "username"},
            {
                "kind": "aggregate",
                "function": aggregate,
                "table": "entry",
                "column": aggregate_column,
                "alias": "aggregate_value",
            },
        ],
        "where": {
            "left": {"kind": "column", "table": "entry", "column": "year_id"},
            "operator": "eq",
            "value": {"kind": "literal", "value": year},
        },
        "group_by": [{"kind": "column", "table": "owner", "column": "username"}],
        "having": {
            "left": {"kind": "output", "name": "aggregate_value"},
            "operator": "gte",
            "value": {"kind": "literal", "value": threshold},
        },
        "order_by": [
            {
                "expression": {"kind": "output", "name": "aggregate_value"},
                "direction": "desc",
            }
        ],
        "limit": limit,
        "offset": offset,
    }

    compiled = compile_builder_query(definition, CATALOG)

    assert compiled.operation == "select"
    assert compiled.referenced_objects == ("account", "song")
    assert compiled.params == (year, threshold, limit, offset)
    assert detect_sql_operation(compiled.statement.as_string(None)) == "select"


@given(
    operation=st.sampled_from(["insert", "update"]),
    target=st.sampled_from(["song", "current_song"]),
    with_filter=st.booleans(),
    value=st.text(max_size=100),
)
def test_builder_only_allows_safe_writes(operation, target, with_filter, value):
    definition = {
        "operation": operation,
        "from": {"table": target, "alias": "entry"},
        "values": [{"column": "title", "value": {"kind": "literal", "value": value}}],
    }
    if with_filter:
        definition["where"] = {
            "left": {"kind": "column", "table": "entry", "column": "id"},
            "operator": "eq",
            "value": {"kind": "literal", "value": 1},
        }

    invalid = target == "current_song" or (operation == "update" and not with_filter)
    if invalid:
        with pytest.raises(QueryDefinitionError):
            compile_builder_query(definition, CATALOG)
        return

    compiled = compile_builder_query(definition, CATALOG)
    expected_params = (value, 1) if operation == "update" else (value,)
    assert compiled.operation == operation
    assert compiled.referenced_objects == ("song",)
    assert compiled.params == expected_params
    assert detect_sql_operation(compiled.statement.as_string(None)) == operation


@given(
    name=st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,20}", fullmatch=True),
    value=st.one_of(st.integers(), st.text(min_size=1, max_size=100)),
)
def test_named_parameters_bind_only_in_executable_sql(name, value):
    query = (
        f"SELECT @{name}, '@{name}', \"@{name}\", $tag$@{name}$tag$ "
        f"/* @{name} */ -- @{name}\n, @{name}"
    )

    rendered, params = bind_named_sql(
        query,
        {name: value},
        [{"name": name, "type": "text", "required": True}],
    )

    assert params == (str(value), str(value))
    assert rendered.count("%s") == len(params)


@given(
    operation=st.sampled_from(sorted(SQL_OPERATIONS)),
    use_cte=st.booleans(),
    noise=st.text(alphabet=string.ascii_letters + string.digits + " ;'@", max_size=30),
)
def test_operation_detection_ignores_comments_and_cte_bodies(operation, use_cte, noise):
    statement = SQL_OPERATIONS[operation]
    if use_cte:
        statement = f"WITH source AS (SELECT 1) {statement}"
    query = f"/* {noise} */ -- {noise}\n{statement}"

    assert detect_sql_operation(query) == operation
    assert validate_raw_sql(query) == operation


@given(
    payload=st.text(
        alphabet=string.ascii_letters + string.digits + " ;@",
        min_size=1,
        max_size=50,
    )
)
def test_statement_validation_distinguishes_data_from_additional_statements(payload):
    assert validate_raw_sql(f"SELECT '{payload}'") == "select"

    with pytest.raises(QueryDefinitionError):
        validate_raw_sql("SELECT 1; SELECT 2")


coercion_cases = st.one_of(
    st.integers().map(lambda value: (str(value), "integer", value)),
    st.floats(allow_nan=False, allow_infinity=False).map(
        lambda value: (str(value), "number", value)
    ),
    st.sampled_from(
        [
            ("true", "boolean", True),
            ("1", "boolean", True),
            ("yes", "boolean", True),
            ("on", "boolean", True),
            ("false", "boolean", False),
            ("0", "boolean", False),
            ("no", "boolean", False),
            ("off", "boolean", False),
        ]
    ),
    st.dates().map(lambda value: (value.isoformat(), "date", value)),
    st.datetimes(timezones=st.none()).map(lambda value: (value.isoformat(), "datetime", value)),
    st.lists(st.integers(), max_size=30).map(
        lambda values: ([str(value) for value in values], "integer[]", values)
    ),
    st.lists(st.text(max_size=30), max_size=30).map(lambda values: (values, "text[]", values)),
)


@given(case=coercion_cases)
def test_parameter_coercion_preserves_declared_value_semantics(case):
    raw, parameter_type, expected = case

    assert coerce_parameter_value(raw, parameter_type) == expected


@given(value=st.booleans())
def test_booleans_are_not_accepted_as_integers(value):
    with pytest.raises(QueryDefinitionError):
        coerce_parameter_value(value, "integer")
