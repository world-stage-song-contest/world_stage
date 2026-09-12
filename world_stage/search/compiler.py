from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import LiteralString, cast
from zoneinfo import ZoneInfo

from psycopg import sql

from .catalog import ENTRY_CTE, SOURCES, VISIBILITY
from .query import (
    FIELDS,
    TEXT_OPERATORS,
    TYPES,
    Comparison,
    Group,
    Negation,
    Query,
    QueryError,
    Value,
    pattern_parts,
)


def trusted(value: str) -> sql.SQL:
    return sql.SQL(cast(LiteralString, value))


@dataclass(frozen=True)
class CompiledSearch:
    statement: sql.Composed
    params: dict


SQL_TYPES = {
    "text": "text",
    "number": "double precision",
    "boolean": "boolean",
    "date": "date",
    "time": "time",
    "datetime": "timestamptz",
}


class Compiler:
    def __init__(self, now: dt.datetime, timezone: ZoneInfo):
        self.now = now
        self.timezone = timezone
        self.params = {}
        self.joins = []
        self.category = "entry"

    def parameter(self, value):
        name = f"p{len(self.params)}"
        self.params[name] = value
        return sql.Placeholder(name)

    def field(self, name):
        source = SOURCES[self.category]
        if name == "type":
            return self.parameter(self.category), False
        if name == "id":
            return trusted(source.id), False
        if name == "name":
            return trusted(source.name), False
        if name in source.fields:
            return trusted(source.fields[name]), FIELDS[name].many
        return trusted("NULL::" + SQL_TYPES[FIELDS[name].type]), FIELDS[name].many

    def normalized(self, expr, options):
        expr = sql.SQL("normalize({}, NFC)").format(expr)
        if options.accent == "insensitive":
            expr = sql.SQL("unaccent({})").format(expr)
        if options.case == "insensitive":
            expr = sql.SQL("lower({})").format(expr)
        return expr

    def escaped_pattern_literal(self, value, options):
        expr = self.normalized(self.literal(Value("string", value), "text"), options)
        for character in ("\\", "%", "_"):
            expr = sql.SQL("replace({}, {}, {})").format(
                expr, self.parameter(character), self.parameter("\\" + character)
            )
        return expr

    def literal(self, value, type_name):
        raw = value.value
        if value.kind == "temporal_sentinel":
            if raw == "now":
                raw = self.now
            else:
                raw = self.now.astimezone(self.timezone).date() + dt.timedelta(
                    days={"today": 0, "tomorrow": 1, "yesterday": -1}[raw]
                )
        elif value.kind == "date":
            raw = dt.date.fromisoformat(raw)
        elif value.kind == "datetime":
            raw = dt.datetime.fromisoformat(raw)
            if raw.tzinfo is None:
                candidates = {
                    candidate.astimezone(dt.UTC)
                    for fold in (0, 1)
                    if (candidate := raw.replace(tzinfo=self.timezone, fold=fold))
                    .astimezone(dt.UTC)
                    .astimezone(self.timezone)
                    .replace(tzinfo=None)
                    == raw
                }
                if len(candidates) != 1:
                    raise QueryError(
                        "Local datetime is ambiguous or nonexistent; specify an offset"
                    )
                raw = candidates.pop()
        elif value.kind == "time":
            raw = dt.time.fromisoformat(raw)
            if raw.tzinfo is not None:
                raise QueryError("Offset times require an offset-time field; none is exposed yet")
        return sql.SQL("{}::{}").format(self.parameter(raw), trusted(SQL_TYPES[type_name]))

    def operand(self, value, type_name):
        return (
            self.field(value.value)[0] if value.kind == "field" else self.literal(value, type_name)
        )

    def text_values(self):
        source = SOURCES[self.category]
        parts = []
        for name, weight in source.text_fields:
            if name == "event_name":
                expr, many = trusted("e.special_name"), False
            elif name == "short_name":
                expr = trusted(
                    "s.short_name" if self.category == "show" else "y.special_short_name"
                )
                many = False
            else:
                expr, many = self.field(name)
            if not many:
                expr = sql.SQL("ARRAY[{}::text]").format(expr)
            parts.append(
                sql.SQL("SELECT value, {} AS weight FROM unnest({}) value").format(
                    sql.Literal(weight), expr
                )
            )
        return sql.SQL("({}) values_to_search").format(sql.SQL(" UNION ALL ").join(parts))

    def comparison(self, node: Comparison):
        for value in (node.left, node.right):
            if value.kind == "field" and self.category not in FIELDS[value.value].categories:
                return trusted("NULL::boolean"), trusted("0.0")
        if node.operator == "in":
            comparisons = [
                self.comparison(Comparison("equals", node.left, item, node.type, node.options))
                for item in node.right.value
            ]
            return (
                sql.SQL("({})").format(sql.SQL(" OR ").join(c[0] for c in comparisons)),
                trusted("0.0"),
            )
        left, right = node.left, node.right
        if left.kind == "null":
            left, right = right, left
        many = left.kind == "field" and FIELDS[left.value].many
        options = node.options
        expr = (
            self.operand(left, node.type) if left.value != "text" or left.kind != "field" else None
        )
        if left.kind == "field" and left.value == "text":
            values = self.text_values()
        elif many:
            values = sql.SQL(
                "(SELECT value, 1 AS weight FROM unnest({}) value) values_to_search"
            ).format(expr)
        else:
            values = sql.SQL("(SELECT {} AS value, 1 AS weight) values_to_search").format(expr)
        if right.kind == "null" and options.nulls == "distinct":
            predicate = (
                sql.SQL("NOT EXISTS (SELECT 1 FROM {} WHERE value IS NOT NULL)").format(values)
                if many
                else sql.SQL("{} IS NULL").format(expr)
            )
            return predicate, trusted("0.0")

        a = trusted("value")
        b = self.operand(right, node.type)
        if options.nulls == "as_empty":
            empty = self.literal(Value(node.type, TYPES[node.type].empty), node.type)
            a = sql.SQL("COALESCE({}, {})").format(a, empty)
            b = sql.SQL("COALESCE({}, {})").format(b, empty)
            if many:
                values = sql.SQL("""(SELECT * FROM {0}
                    UNION ALL SELECT {1}, 1 WHERE NOT EXISTS (
                        SELECT 1 FROM {0} WHERE value IS NOT NULL
                    )) values_with_empty""").format(values, empty)
        if node.type == "text":
            a, b = self.normalized(a, options), self.normalized(b, options)
        op = node.operator
        quality = trusted("1.0")
        if op in (
            "equals",
            "less_than",
            "less_than_or_equal",
            "greater_than",
            "greater_than_or_equal",
        ):
            symbol = {
                "equals": "=",
                "less_than": "<",
                "less_than_or_equal": "<=",
                "greater_than": ">",
                "greater_than_or_equal": ">=",
            }[op]
            predicate = sql.SQL("{} {} {}").format(a, trusted(symbol), b)
        elif op in ("like", "starts_with", "ends_with", "contains"):
            if op == "like":
                parts = pattern_parts(right.value)
            else:
                parts = (("literal", right.value),)
                if op in ("ends_with", "contains"):
                    parts = (("wildcard", "%"),) + parts
                if op in ("starts_with", "contains"):
                    parts += (("wildcard", "%"),)
            b = sql.SQL("({})").format(
                sql.SQL(" || ").join(
                    self.parameter(value)
                    if kind == "wildcard"
                    else self.escaped_pattern_literal(value, options)
                    for kind, value in parts
                )
            )
            predicate = sql.SQL("{} LIKE {} ESCAPE E'\\\\'").format(a, b)
        elif op == "phrase":
            a = sql.SQL("regexp_replace({}, '[[:space:]]+', ' ', 'g')").format(a)
            b = sql.SQL("regexp_replace({}, '[[:space:]]+', ' ', 'g')").format(b)
            predicate = sql.SQL("""to_tsvector('simple', {0}) @@ phraseto_tsquery('simple', {1})
                AND strpos({0}, {1}) > 0""").format(a, b)
        else:
            similarity = sql.SQL("""COALESCE((
                SELECT max(1.0 - distance / greatest(length(candidate), length({1}), 1)::float)
                FROM (
                    SELECT candidate, CASE WHEN length(candidate) <= 128 AND length({1}) <= 255 THEN
                        levenshtein_less_equal(
                            candidate, {1}, least(3, ceil(length({1}) * 0.3)::int))
                        ELSE 129 END AS distance
                    FROM unnest(ARRAY[{0}] || regexp_split_to_array({0}, '[[:space:]]+')) candidate
                    WHERE abs(length(candidate) - length({1})) <= 3
                ) distances
                WHERE distance <= least(3, ceil(length({1}) * 0.3)::int)
            ), 0.0)""").format(a, b)
            predicate = sql.SQL("strpos({0}, {1}) > 0 OR (length({1}) >= 3 AND {2} > 0)").format(
                a, b, similarity
            )
            quality = sql.SQL(
                "CASE WHEN {0} = {1} THEN 4.0 WHEN starts_with({0}, {1}) THEN 3.0 "
                "WHEN strpos({0}, {1}) > 0 THEN 2.0 WHEN length({1}) >= 3 THEN {2} ELSE 0 END"
            ).format(a, b, similarity)
        alias = f"match_{len(self.joins)}"
        scored = node.type == "text" and (
            op in TEXT_OPERATORS
            or (left.kind == "field" and left.value in ("text", "title", "name", "artist"))
        )
        score = (
            sql.SQL("COALESCE(max(CASE WHEN ({}) THEN weight * {} ELSE 0 END), 0)").format(
                predicate, quality
            )
            if scored
            else trusted("0.0")
        )
        self.joins.append(
            sql.SQL(
                "CROSS JOIN LATERAL (SELECT bool_or({}) AS matched, {} AS score FROM {}) {} "
            ).format(predicate, score, values, sql.Identifier(alias))
        )
        return sql.Identifier(alias, "matched"), sql.Identifier(alias, "score")

    def expression(self, node):
        if isinstance(node, Negation):
            match, _score = self.expression(node.operand)
            return sql.SQL("NOT ({})").format(match), trusted("0.0")
        if isinstance(node, Group):
            children = [self.expression(child) for child in node.operands]
            match = sql.SQL("({})").format(
                trusted(f" {node.kind.upper()} ").join(c[0] for c in children)
            )
            if node.kind == "and":
                score = sql.SQL("({})").format(sql.SQL(" + ").join(c[1] for c in children))
            else:
                score = sql.SQL("greatest({})").format(sql.SQL(", ").join(c[1] for c in children))
            return match, score
        return self.comparison(node)

    def compile(self, query: Query, limit: int, offset: int):
        branches = []
        for category, source in SOURCES.items():
            self.category = category
            self.joins = []
            match, score = self.expression(query.where)
            columns = []
            for index, order in enumerate(query.order):
                expr = score if order.field == "relevance" else self.field(order.field)[0]
                type_name = FIELDS[order.field].type
                columns.append(
                    sql.SQL("{}::{} AS {}").format(
                        expr, trusted(SQL_TYPES[type_name]), sql.Identifier(f"sort_{index}")
                    )
                )
            branches.append(
                sql.SQL("""
                SELECT {category}::text AS type, {id} AS id, {name} AS name,
                       {route} AS data, {score}::double precision AS relevance, {columns}
                FROM {source} {joins} WHERE ({visibility}) AND ({match})
            """).format(
                    category=self.parameter(category),
                    id=trusted(source.id),
                    name=trusted(source.name),
                    route=trusted(source.route),
                    score=score,
                    columns=sql.SQL(", ").join(columns),
                    source=trusted(source.from_sql),
                    joins=sql.SQL(" ").join(self.joins),
                    visibility=trusted(VISIBILITY[category]),
                    match=match,
                )
            )
        ordering = [
            sql.SQL("{} {} NULLS LAST").format(
                sql.Identifier(f"sort_{index}"),
                trusted("ASC" if order.direction == "ascending" else "DESC"),
            )
            for index, order in enumerate(query.order)
        ]
        statement = sql.SQL(
            "{} SELECT * FROM ({}) results ORDER BY {}, type, id LIMIT {} OFFSET {}"
        ).format(
            trusted(ENTRY_CTE),
            sql.SQL(" UNION ALL ").join(branches),
            sql.SQL(", ").join(ordering),
            self.parameter(limit),
            self.parameter(offset),
        )
        return CompiledSearch(statement, self.params)


def compile_search(
    query: Query,
    *,
    limit: int = 51,
    offset: int = 0,
    now: dt.datetime | None = None,
    timezone: str = "Europe/Warsaw",
) -> CompiledSearch:
    return Compiler(now or dt.datetime.now(dt.UTC), ZoneInfo(timezone)).compile(
        query, limit, offset
    )
