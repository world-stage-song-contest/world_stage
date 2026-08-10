from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, LiteralString, cast

from psycopg import sql
from psycopg.abc import Params
from psycopg.rows import DictRow


class QueryDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class SchemaObject:
    name: str
    kind: str
    columns: frozenset[str]


@dataclass(frozen=True)
class CompiledQuery:
    statement: sql.Composed
    params: Params
    operation: str
    referenced_objects: tuple[str, ...]


def load_schema_catalog(cursor) -> dict[str, SchemaObject]:
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
    objects = {
        row["table_name"]: {"kind": "view" if "VIEW" in row["table_type"] else "table"}
        for row in cursor.fetchall()
    }
    cursor.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND LEFT(table_name, 3) <> 'pg_'
        ORDER BY table_name, ordinal_position
        """
    )
    columns: dict[str, set[str]] = {name: set() for name in objects}
    for row in cursor.fetchall():
        columns.setdefault(row["table_name"], set()).add(row["column_name"])
    return {
        name: SchemaObject(name, item["kind"], frozenset(columns.get(name, set())))
        for name, item in objects.items()
    }


def _object(name: Any, catalog: dict[str, SchemaObject]) -> SchemaObject:
    if not isinstance(name, str) or name not in catalog:
        raise QueryDefinitionError(f"Unknown table or view: {name!r}")
    return catalog[name]


def _alias(name: Any) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise QueryDefinitionError(f"Invalid table alias: {name!r}")
    return name


class _Compiler:
    def __init__(
        self,
        definition: dict[str, Any],
        catalog: dict[str, SchemaObject],
        values: dict[str, Any],
        parameter_schema: list[dict[str, Any]],
    ):
        self.definition = definition
        self.catalog = catalog
        self.values = values
        self.parameter_schema = {
            item.get("name"): item for item in parameter_schema if isinstance(item, dict)
        }
        self.params: list[Any] = []
        self.aliases: dict[str, SchemaObject] = {}
        self.referenced: set[str] = set()
        self.outputs: dict[str, sql.Composable] = {}

    def compile(self) -> CompiledQuery:
        operation = str(self.definition.get("operation", "select")).lower()
        if operation == "select":
            statement = self._select()
        elif operation == "insert":
            statement = self._insert()
        elif operation == "update":
            statement = self._update()
        else:
            raise QueryDefinitionError("The builder supports SELECT, INSERT, and UPDATE")
        return CompiledQuery(
            statement=statement,
            params=tuple(self.params),
            operation=operation,
            referenced_objects=tuple(sorted(self.referenced)),
        )

    def _base(self, *, writable: bool = False) -> tuple[SchemaObject, str, sql.Composed]:
        base = self.definition.get("from")
        if not isinstance(base, dict):
            raise QueryDefinitionError("A base table is required")
        obj = _object(base.get("table"), self.catalog)
        if writable and obj.kind != "table":
            raise QueryDefinitionError("INSERT and UPDATE require a table, not a view")
        alias = _alias(base.get("alias") or obj.name)
        self.aliases[alias] = obj
        self.referenced.add(obj.name)
        return (
            obj,
            alias,
            sql.SQL("{} AS {}").format(sql.Identifier(obj.name), sql.Identifier(alias)),
        )

    def _column(self, spec: Any) -> sql.Composed:
        if not isinstance(spec, dict):
            raise QueryDefinitionError("A column reference must be an object")
        alias = spec.get("table")
        column = spec.get("column")
        if alias not in self.aliases or not isinstance(column, str):
            raise QueryDefinitionError(f"Unknown column reference: {alias}.{column}")
        if column not in self.aliases[alias].columns:
            raise QueryDefinitionError(f"Unknown column: {alias}.{column}")
        return sql.SQL("{}.{}").format(sql.Identifier(alias), sql.Identifier(column))

    def _expression(self, spec: Any, *, allow_output: bool = False) -> sql.Composable:
        if not isinstance(spec, dict):
            raise QueryDefinitionError("Invalid expression")
        kind = spec.get("kind", "column")
        if kind == "column":
            return self._column(spec)
        if kind == "output" and allow_output:
            name = spec.get("name")
            if name not in self.outputs:
                raise QueryDefinitionError(f"Unknown result alias: {name}")
            return self.outputs[name]
        if kind != "aggregate":
            raise QueryDefinitionError(f"Unsupported expression type: {kind}")
        function = str(spec.get("function", "")).lower()
        if function not in {"count", "sum", "avg", "min", "max"}:
            raise QueryDefinitionError(f"Unsupported aggregate: {function}")
        if spec.get("column") == "*":
            if function != "count":
                raise QueryDefinitionError("Only COUNT can use *")
            if spec.get("distinct"):
                raise QueryDefinitionError("COUNT DISTINCT requires a column")
            inner: sql.Composable = sql.SQL("*")
        else:
            inner = self._column(spec)
        distinct = sql.SQL("DISTINCT ") if spec.get("distinct") else sql.SQL("")
        function_sql = sql.SQL(cast(LiteralString, function.upper()))
        return sql.SQL("{}({}{})").format(function_sql, distinct, inner)

    def _parameter(self, name: Any) -> Any:
        if not isinstance(name, str) or name not in self.parameter_schema:
            raise QueryDefinitionError(f"Undefined parameter: {name!r}")
        spec = self.parameter_schema[name]
        value = self.values.get(name, spec.get("default"))
        if value in (None, "") and spec.get("required", True):
            raise QueryDefinitionError(f"Parameter {name!r} is required")
        return coerce_parameter_value(value, str(spec.get("type", "text")))

    def _value(self, spec: Any) -> sql.Placeholder:
        if not isinstance(spec, dict):
            raise QueryDefinitionError("Invalid filter value")
        kind = spec.get("kind", "literal")
        if kind == "parameter":
            value = self._parameter(spec.get("name"))
        elif kind == "literal":
            value = spec.get("value")
        else:
            raise QueryDefinitionError(f"Unsupported value type: {kind}")
        self.params.append(value)
        return sql.Placeholder()

    def _pagination_value(self, keyword: str, spec: Any) -> sql.Placeholder:
        if not isinstance(spec, dict):
            spec = {"kind": "literal", "value": spec}
        value: Any
        if spec.get("kind", "literal") == "parameter":
            value = self._parameter(spec.get("name"))
        else:
            value = spec.get("value")
        if value is None or isinstance(value, bool):
            raise QueryDefinitionError(f"{keyword.title()} must be a non-negative integer")
        try:
            integer = int(value)
        except (TypeError, ValueError) as exc:
            raise QueryDefinitionError(f"{keyword.title()} must be a non-negative integer") from exc
        if integer < 0:
            raise QueryDefinitionError(f"{keyword.title()} must be a non-negative integer")
        self.params.append(integer)
        return sql.Placeholder()

    def _condition(self, condition: Any, *, allow_output: bool = False) -> sql.Composable:
        if not isinstance(condition, dict):
            raise QueryDefinitionError("Invalid filter condition")
        if "conditions" in condition:
            children = condition.get("conditions")
            if not isinstance(children, list) or not children:
                raise QueryDefinitionError("A filter group cannot be empty")
            logic = str(condition.get("logic", "and")).lower()
            if logic not in {"and", "or"}:
                raise QueryDefinitionError("Filter groups must use AND or OR")
            separator = sql.SQL(cast(LiteralString, f" {logic.upper()} "))
            joined = separator.join(
                self._condition(child, allow_output=allow_output) for child in children
            )
            result: sql.Composable = sql.SQL("({})").format(joined)
            return sql.SQL("NOT {} ").format(result) if condition.get("not") else result

        left = self._expression(condition.get("left"), allow_output=allow_output)
        operator = str(condition.get("operator", "eq")).lower()
        if operator == "is_null":
            return sql.SQL("{} IS NULL").format(left)
        if operator == "is_not_null":
            return sql.SQL("{} IS NOT NULL").format(left)
        if operator in {"in", "not_in"}:
            value_spec = condition.get("value")
            placeholder = self._value(value_spec)
            if operator == "not_in":
                return sql.SQL("{} <> ALL({})").format(left, placeholder)
            return sql.SQL("{} = ANY({})").format(left, placeholder)
        operators = {
            "eq": "=",
            "ne": "<>",
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
            "like": "LIKE",
            "ilike": "ILIKE",
            "contains": "ILIKE",
            "starts_with": "ILIKE",
            "ends_with": "ILIKE",
        }
        if operator not in operators:
            raise QueryDefinitionError(f"Unsupported filter operator: {operator}")
        value_spec = condition.get("value")
        if operator in {"contains", "starts_with", "ends_with"}:
            if not isinstance(value_spec, dict):
                raise QueryDefinitionError("Invalid text filter value")
            value_spec = dict(value_spec)
            if value_spec.get("kind", "literal") == "parameter":
                value = self._parameter(value_spec.get("name"))
            else:
                value = value_spec.get("value")
            text = "" if value is None else str(value)
            value = {
                "contains": f"%{text}%",
                "starts_with": f"{text}%",
                "ends_with": f"%{text}",
            }[operator]
            self.params.append(value)
            placeholder: sql.Composable = sql.Placeholder()
        else:
            placeholder = self._value(value_spec)
        operator_sql = sql.SQL(cast(LiteralString, operators[operator]))
        return sql.SQL("{} {} {}").format(left, operator_sql, placeholder)

    def _where(self, value: Any, *, allow_output: bool = False) -> sql.Composable | None:
        if value in (None, [], {}):
            return None
        if isinstance(value, list):
            value = {"logic": "and", "conditions": value}
        return self._condition(value, allow_output=allow_output)

    def _joins(self) -> list[sql.Composable]:
        result: list[sql.Composable] = []
        for join in self.definition.get("joins", []):
            if not isinstance(join, dict):
                raise QueryDefinitionError("Invalid join")
            obj = _object(join.get("table"), self.catalog)
            alias = _alias(join.get("alias") or obj.name)
            if alias in self.aliases:
                raise QueryDefinitionError(f"Duplicate table alias: {alias}")
            join_type = str(join.get("type", "inner")).lower()
            if join_type not in {"inner", "left"}:
                raise QueryDefinitionError("Only INNER and LEFT joins are supported")
            # Register the target before compiling its ON clause.
            self.aliases[alias] = obj
            self.referenced.add(obj.name)
            on = join.get("on")
            if not isinstance(on, list) or not on:
                raise QueryDefinitionError("Every join needs an ON condition")
            predicates = []
            for item in on:
                if not isinstance(item, dict) or item.get("operator", "eq") != "eq":
                    raise QueryDefinitionError("Join conditions currently support equality")
                predicates.append(
                    sql.SQL("{} = {}").format(
                        self._column(item.get("left")), self._column(item.get("right"))
                    )
                )
            result.append(
                sql.SQL("{} JOIN {} AS {} ON {}").format(
                    sql.SQL(cast(LiteralString, join_type.upper())),
                    sql.Identifier(obj.name),
                    sql.Identifier(alias),
                    sql.SQL(" AND ").join(predicates),
                )
            )
        return result

    def _select(self) -> sql.Composed:
        _obj, _alias_name, base = self._base()
        joins = self._joins()
        columns = self.definition.get("columns")
        if not isinstance(columns, list) or not columns:
            raise QueryDefinitionError("Select at least one result column")
        rendered_columns = []
        for item in columns:
            expression = self._expression(item)
            alias = item.get("alias") if isinstance(item, dict) else None
            if alias:
                alias = _alias(alias)
                if alias in self.outputs:
                    raise QueryDefinitionError(f"Duplicate result alias: {alias}")
                self.outputs[alias] = expression
                rendered_columns.append(
                    sql.SQL("{} AS {}").format(expression, sql.Identifier(alias))
                )
            else:
                rendered_columns.append(expression)
        pieces: list[sql.Composable] = [
            sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(rendered_columns), base)
        ]
        pieces.extend(joins)
        where = self._where(self.definition.get("where"))
        if where is not None:
            pieces.append(sql.SQL("WHERE {}").format(where))
        group_by = self.definition.get("group_by", [])
        if group_by:
            pieces.append(
                sql.SQL("GROUP BY {}").format(
                    sql.SQL(", ").join(self._expression(item) for item in group_by)
                )
            )
        having = self._where(self.definition.get("having"), allow_output=True)
        if having is not None:
            if not group_by and not any(
                isinstance(item, dict) and item.get("kind") == "aggregate" for item in columns
            ):
                raise QueryDefinitionError("HAVING requires grouping or aggregate results")
            pieces.append(sql.SQL("HAVING {}").format(having))
        order_items = []
        for item in self.definition.get("order_by", []):
            if not isinstance(item, dict):
                raise QueryDefinitionError("Invalid sort expression")
            direction = str(item.get("direction", "asc")).lower()
            if direction not in {"asc", "desc"}:
                raise QueryDefinitionError("Sort direction must be ascending or descending")
            order_items.append(
                sql.SQL("{} {}").format(
                    self._expression(item.get("expression"), allow_output=True),
                    sql.SQL(cast(LiteralString, direction.upper())),
                )
            )
        if order_items:
            pieces.append(sql.SQL("ORDER BY {}").format(sql.SQL(", ").join(order_items)))
        for keyword in ("limit", "offset"):
            value = self.definition.get(keyword)
            if value is None:
                continue
            placeholder = self._pagination_value(keyword, value)
            pieces.append(sql.SQL(f"{keyword.upper()} {{}}").format(placeholder))
        return sql.SQL(" ").join(pieces)

    def _insert(self) -> sql.Composed:
        obj, alias, _base = self._base(writable=True)
        values = self.definition.get("values")
        if not isinstance(values, list) or not values:
            raise QueryDefinitionError("INSERT needs at least one value")
        columns = []
        placeholders = []
        seen_columns: set[str] = set()
        for item in values:
            if not isinstance(item, dict) or item.get("column") not in obj.columns:
                raise QueryDefinitionError("Unknown INSERT column")
            if item["column"] in seen_columns:
                raise QueryDefinitionError(f"Duplicate INSERT column: {item['column']}")
            seen_columns.add(item["column"])
            columns.append(sql.Identifier(item["column"]))
            placeholders.append(self._value(item.get("value")))
        return sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(obj.name), sql.SQL(", ").join(columns), sql.SQL(", ").join(placeholders)
        )

    def _update(self) -> sql.Composed:
        obj, alias, _base = self._base(writable=True)
        values = self.definition.get("values")
        if not isinstance(values, list) or not values:
            raise QueryDefinitionError("UPDATE needs at least one value")
        assignments = []
        seen_columns: set[str] = set()
        for item in values:
            if not isinstance(item, dict) or item.get("column") not in obj.columns:
                raise QueryDefinitionError("Unknown UPDATE column")
            if item["column"] in seen_columns:
                raise QueryDefinitionError(f"Duplicate UPDATE column: {item['column']}")
            seen_columns.add(item["column"])
            assignments.append(
                sql.SQL("{} = {}").format(
                    sql.Identifier(item["column"]), self._value(item.get("value"))
                )
            )
        where = self._where(self.definition.get("where"))
        if where is None:
            raise QueryDefinitionError("UPDATE requires at least one filter")
        return sql.SQL("UPDATE {} AS {} SET {} WHERE {} RETURNING *").format(
            sql.Identifier(obj.name),
            sql.Identifier(alias),
            sql.SQL(", ").join(assignments),
            where,
        )


def compile_builder_query(
    definition: dict[str, Any],
    catalog: dict[str, SchemaObject],
    values: dict[str, Any] | None = None,
    parameter_schema: list[dict[str, Any]] | None = None,
) -> CompiledQuery:
    return _Compiler(definition, catalog, values or {}, parameter_schema or []).compile()


def coerce_parameter_value(value: Any, parameter_type: str) -> Any:
    if value is None:
        return None
    if parameter_type in {"integer", "bigint"}:
        if isinstance(value, bool):
            raise QueryDefinitionError("A boolean is not an integer")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise QueryDefinitionError(f"Invalid integer value: {value!r}") from exc
    if parameter_type in {"number", "numeric", "decimal"}:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise QueryDefinitionError(f"Invalid numeric value: {value!r}") from exc
    if parameter_type == "boolean":
        if isinstance(value, bool):
            return value
        lowered = str(value).lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise QueryDefinitionError(f"Invalid boolean value: {value!r}")
    if parameter_type == "date":
        try:
            return datetime.date.fromisoformat(str(value))
        except ValueError as exc:
            raise QueryDefinitionError(f"Invalid date value: {value!r}") from exc
    if parameter_type in {"datetime", "timestamp"}:
        try:
            return datetime.datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise QueryDefinitionError(f"Invalid date/time value: {value!r}") from exc
    if parameter_type in {"integer[]", "text[]"}:
        if not isinstance(value, list):
            raise QueryDefinitionError("List parameters must be arrays")
        if parameter_type == "integer[]":
            return [int(item) for item in value]
        return [str(item) for item in value]
    return str(value)


def bind_named_sql(
    query: str,
    values: dict[str, Any],
    parameter_schema: list[dict[str, Any]],
) -> tuple[str, tuple[Any, ...]]:
    specs: dict[str, dict[str, Any]] = {
        item["name"]: item
        for item in parameter_schema
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    output: list[str] = []
    params: list[Any] = []
    i = 0
    state = "code"
    dollar_tag = ""
    while i < len(query):
        if state == "code":
            if query.startswith("--", i):
                state = "line_comment"
                output.append("--")
                i += 2
                continue
            if query.startswith("/*", i):
                state = "block_comment"
                output.append("/*")
                i += 2
                continue
            if query[i] == "'":
                state = "single"
                output.append(query[i])
                i += 1
                continue
            if query[i] == '"':
                state = "double"
                output.append(query[i])
                i += 1
                continue
            dollar = re.match(r"\$[A-Za-z_0-9]*\$", query[i:])
            if dollar:
                dollar_tag = dollar.group(0)
                state = "dollar"
                output.append(dollar_tag)
                i += len(dollar_tag)
                continue
            if query[i] == "@":
                match = re.match(r"@([A-Za-z_][A-Za-z0-9_]*)", query[i:])
                if match:
                    name = match.group(1)
                    if name not in specs:
                        raise QueryDefinitionError(f"Undefined parameter: {name}")
                    spec = specs[name]
                    value = values.get(name, spec.get("default"))
                    if value in (None, "") and spec.get("required", True):
                        raise QueryDefinitionError(f"Parameter {name!r} is required")
                    params.append(coerce_parameter_value(value, str(spec.get("type", "text"))))
                    output.append("%s")
                    i += len(match.group(0))
                    continue
        elif state == "single":
            if query[i] == "'":
                if i + 1 < len(query) and query[i + 1] == "'":
                    output.append("''")
                    i += 2
                    continue
                state = "code"
        elif state == "double":
            if query[i] == '"':
                if i + 1 < len(query) and query[i + 1] == '"':
                    output.append('""')
                    i += 2
                    continue
                state = "code"
        elif state == "line_comment" and query[i] == "\n":
            state = "code"
        elif state == "block_comment" and query.startswith("*/", i):
            output.append("*/")
            i += 2
            state = "code"
            continue
        elif state == "dollar" and query.startswith(dollar_tag, i):
            output.append(dollar_tag)
            i += len(dollar_tag)
            state = "code"
            continue
        output.append(query[i])
        i += 1
    return "".join(output), tuple(params)


def _mask_sql_literals_and_comments(query: str) -> str:
    """Keep SQL structure while hiding tokens inside strings and comments."""
    output = list(query)
    i = 0
    state = "code"
    dollar_tag = ""
    while i < len(query):
        if state == "code":
            if query.startswith("--", i):
                state = "line_comment"
            elif query.startswith("/*", i):
                state = "block_comment"
            elif query[i] == "'":
                state = "single"
            elif query[i] == '"':
                state = "double"
            else:
                dollar = re.match(r"\$[A-Za-z_0-9]*\$", query[i:])
                if dollar:
                    dollar_tag = dollar.group(0)
                    state = "dollar"
                else:
                    i += 1
                    continue
        elif state == "line_comment" and query[i] == "\n":
            state = "code"
            i += 1
            continue
        elif state == "block_comment" and query.startswith("*/", i):
            output[i] = output[i + 1] = " "
            i += 2
            state = "code"
            continue
        elif state == "single" and query[i] == "'":
            if i + 1 < len(query) and query[i + 1] == "'":
                output[i] = output[i + 1] = " "
                i += 2
                continue
            output[i] = " "
            i += 1
            state = "code"
            continue
        elif state == "double" and query[i] == '"':
            if i + 1 < len(query) and query[i + 1] == '"':
                output[i] = output[i + 1] = " "
                i += 2
                continue
            output[i] = " "
            i += 1
            state = "code"
            continue
        elif state == "dollar" and query.startswith(dollar_tag, i):
            for index in range(i, i + len(dollar_tag)):
                output[index] = " "
            i += len(dollar_tag)
            state = "code"
            continue
        output[i] = "\n" if query[i] == "\n" else " "
        i += 1
    return "".join(output)


def detect_sql_operation(query: str) -> str:
    masked = _mask_sql_literals_and_comments(query)
    depth = 0
    words: list[tuple[str, int]] = []
    for match in re.finditer(r"[()]|[A-Za-z_][A-Za-z0-9_]*", masked):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            words.append((token.lower(), match.start()))
    if not words:
        return "other"
    if words[0][0] != "with":
        return words[0][0] if words[0][0] in {"select", "insert", "update", "delete"} else "other"
    for word, _position in words[1:]:
        if word in {"select", "insert", "update", "delete"}:
            return word
    return "other"


def validate_raw_sql(query: str) -> str:
    masked = _mask_sql_literals_and_comments(query)
    statements = [part for part in masked.split(";") if part.strip()]
    if len(statements) != 1:
        raise QueryDefinitionError("The SQL editor accepts exactly one statement")
    operation = detect_sql_operation(query)
    # A data-modifying CTE can have SELECT as its outer statement. Treat it as
    # DELETE for confirmation and audit purposes if any executable DELETE is present.
    if re.search(r"\bDELETE\b", masked, flags=re.I):
        operation = "delete"
    if operation not in {"select", "insert", "update", "delete"}:
        raise QueryDefinitionError("Only SELECT, INSERT, UPDATE, and DELETE statements are allowed")
    return operation


def query_fingerprint(query: str | dict[str, Any]) -> str:
    if isinstance(query, dict):
        normalized = json.dumps(query, sort_keys=True, separators=(",", ":"))
    else:
        normalized = re.sub(r"\s+", " ", query).strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def json_safe_rows(rows: list[DictRow]) -> list[dict[str, Any]]:
    def safe(value: Any) -> Any:
        if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
            return value.isoformat()
        if isinstance(value, bytes):
            return f"<{len(value)} bytes>"
        if isinstance(value, (list, tuple)):
            return [safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): safe(item) for key, item in value.items()}
        try:
            json.dumps(value)
        except TypeError:
            return str(value)
        return value

    return [{str(key): safe(value) for key, value in row.items()} for row in rows]
