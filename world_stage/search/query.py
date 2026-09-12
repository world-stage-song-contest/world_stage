from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any

RESULT_TYPES = ("entry", "year", "country", "submitter", "artist", "show")
ORDERED = ("less_than", "less_than_or_equal", "greater_than", "greater_than_or_equal")
TEXT_OPERATORS = ("starts_with", "ends_with", "contains", "like", "phrase", "fuzzy")


@dataclass(frozen=True)
class TypeDefinition:
    operators: tuple[str, ...]
    empty: Any = None
    textual: bool = False


TYPES = {
    "text": TypeDefinition(("equals", "in", *TEXT_OPERATORS), "", True),
    "number": TypeDefinition(("equals", "in", *ORDERED), 0),
    "boolean": TypeDefinition(("equals", "in"), False),
    "date": TypeDefinition(("equals", "in", *ORDERED)),
    "time": TypeDefinition(("equals", "in", *ORDERED)),
    "datetime": TypeDefinition(("equals", "in", *ORDERED)),
}


@dataclass(frozen=True)
class Field:
    type: str
    categories: tuple[str, ...]
    many: bool = False
    sortable: bool = True


FIELDS = {
    "type": Field("text", RESULT_TYPES),
    "id": Field("text", RESULT_TYPES),
    "name": Field("text", RESULT_TYPES),
    "text": Field("text", RESULT_TYPES, many=True, sortable=False),
    "title": Field("text", ("entry",)),
    "native_title": Field("text", ("entry",)),
    "artist": Field("text", ("entry", "artist"), many=True, sortable=False),
    "native_name": Field("text", ("artist",)),
    "stage_name": Field("text", ("artist",), many=True, sortable=False),
    "country": Field("text", ("entry", "country", "year"), many=True, sortable=False),
    "code": Field("text", ("country",)),
    "alternative_name": Field("text", ("country",), many=True, sortable=False),
    "submitter": Field("text", ("entry", "submitter")),
    "year": Field("number", ("entry", "year", "show")),
    "special": Field("boolean", ("entry", "year", "show")),
    "language": Field("text", ("entry",), many=True, sortable=False),
    "genre": Field("text", ("entry",), many=True, sortable=False),
    "lyrics": Field("text", ("entry",), many=True, sortable=False),
    "native_lyrics": Field("text", ("entry",)),
    "romanized_lyrics": Field("text", ("entry",)),
    "translated_lyrics": Field("text", ("entry",)),
    "duration": Field("number", ("entry",)),
    "place": Field("number", ("entry",)),
    "main_participant": Field("boolean", ("entry",)),
    "has_video": Field("boolean", ("entry",)),
    "status": Field("text", ("year", "show")),
    "show_type": Field("text", ("show",)),
    "date": Field("date", ("show",)),
    "time": Field("time", ("show",)),
    "starts_at": Field("datetime", ("show",)),
    "voting_opens": Field("datetime", ("show",)),
    "voting_closes": Field("datetime", ("show",)),
    "relevance": Field("number", RESULT_TYPES),
}


class QueryError(ValueError):
    def __init__(self, message: str, path: str = "", code: str = "invalid_query"):
        super().__init__(message)
        self.path = path
        self.code = code


@dataclass(frozen=True)
class Value:
    kind: str
    value: Any = None


@dataclass(frozen=True)
class Options:
    nulls: str = "distinct"
    case: str = "insensitive"
    accent: str = "insensitive"


@dataclass(frozen=True)
class Comparison:
    operator: str
    left: Value
    right: Value
    type: str
    options: Options


@dataclass(frozen=True)
class Group:
    kind: str
    operands: tuple[Expression, ...]


@dataclass(frozen=True)
class Negation:
    operand: Expression


type Expression = Comparison | Group | Negation


@dataclass(frozen=True)
class Order:
    field: str
    direction: str


@dataclass(frozen=True)
class Query:
    where: Expression
    order: tuple[Order, ...] = (Order("relevance", "descending"),)


def object_keys(value, required, optional, path):
    if not isinstance(value, dict):
        raise QueryError("Expected an object", path)
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise QueryError("Missing or unknown properties", path)


def _text(value, path):
    if not isinstance(value, str) or len(value) > 4096:
        raise QueryError("Expected a string of at most 4096 characters", path)
    if "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise QueryError("String contains a character PostgreSQL cannot store", path)
    return value


_DATE = r"\d{4}-\d{2}-\d{2}"
_TIME = r"\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}(?::\d{2})?)?"


def temporal(value: str, kind: str, path: str):
    pattern = {"date": _DATE, "time": _TIME, "datetime": _DATE + "T" + _TIME}[kind]
    if not re.fullmatch(pattern, value, flags=re.ASCII):
        raise QueryError(f"Invalid {kind} literal", path)
    try:
        if kind == "date":
            return dt.date.fromisoformat(value).isoformat()
        offset = re.search(r"([+-])(\d{2})(?::(\d{2}))?$", value)
        if offset and (int(offset[2]) > 23 or int(offset[3] or 0) > 59):
            raise ValueError
        parsed = (dt.time if kind == "time" else dt.datetime).fromisoformat(value)
        result = parsed.isoformat(timespec="seconds" if parsed.second else "minutes")
        if result.endswith("+00:00"):
            return result[:-6] + "Z"
        if offset and result.endswith(":00"):
            return result[:-3]
        return result
    except ValueError as exc:
        raise QueryError(f"Invalid {kind} literal", path) from exc


def _value(data, path):
    if not isinstance(data, dict):
        raise QueryError("Expected a typed operand", path)
    kind = data.get("kind")
    if kind == "placeholder":
        raise QueryError("Fill in placeholder values before running the query", path)
    if kind == "null":
        object_keys(data, {"kind"}, set(), path)
        return Value("null")
    if kind == "field":
        object_keys(data, {"kind", "name"}, set(), path)
        name = data["name"]
        if not isinstance(name, str) or name not in FIELDS or name == "relevance":
            raise QueryError("Unknown or non-filterable field", path + "/name")
        return Value("field", name)
    if kind == "temporal_sentinel":
        object_keys(data, {"kind", "name"}, set(), path)
        name = data["name"]
        if name not in ("now", "today", "tomorrow", "yesterday"):
            raise QueryError("Unknown temporal sentinel", path + "/name")
        return Value(kind, name)
    object_keys(data, {"kind", "value"}, set(), path)
    value = data["value"]
    if kind in ("string", "pattern"):
        return Value(kind, _text(value, path + "/value"))
    if kind == "number":
        if type(value) not in (int, float) or abs(value) > 1e15 or not math.isfinite(value):
            raise QueryError("Expected a finite number between -1e15 and 1e15", path)
        return Value(kind, value)
    if kind == "boolean" and type(value) is bool:
        return Value(kind, value)
    if kind in ("date", "time", "datetime"):
        return Value(kind, temporal(_text(value, path), kind, path))
    if kind == "list":
        if not isinstance(value, list) or not 1 <= len(value) <= 100:
            raise QueryError("IN requires between 1 and 100 values", path)
        if any(
            not isinstance(item, dict) or item.get("kind") in ("list", "field", "pattern")
            for item in value
        ):
            raise QueryError("IN lists contain scalar literals only", path)
        items = tuple(_value(item, f"{path}/value/{i}") for i, item in enumerate(value))
        if any(item.kind in ("list", "field", "pattern") for item in items):
            raise QueryError("IN lists contain scalar literals only", path)
        return Value(kind, items)
    raise QueryError("Invalid operand kind or value", path)


def value_type(value):
    if value.kind == "field":
        return FIELDS[value.value].type
    if value.kind in ("string", "pattern"):
        return "text"
    if value.kind == "temporal_sentinel":
        return "datetime" if value.value == "now" else "date"
    return value.kind


def _expression(data, path, depth, budget):
    budget[0] += 1
    if depth > 16 or budget[0] > 100:
        raise QueryError("Query exceeds 100 expressions or 16 nesting levels", path, "complexity")
    if not isinstance(data, dict):
        raise QueryError("Expected an expression", path)
    kind = data.get("kind")
    if kind == "not":
        object_keys(data, {"kind", "operand"}, set(), path)
        return Negation(_expression(data["operand"], path + "/operand", depth + 1, budget))
    if kind in ("and", "or"):
        object_keys(data, {"kind", "operands"}, set(), path)
        operands = data["operands"]
        if not isinstance(operands, list) or not 1 <= len(operands) <= 100:
            raise QueryError("Boolean groups require 1–100 operands", path)
        children = []
        for i, operand in enumerate(operands):
            child = _expression(operand, f"{path}/operands/{i}", depth + 1, budget)
            children.extend(
                child.operands if isinstance(child, Group) and child.kind == kind else (child,)
            )
        return children[0] if len(children) == 1 else Group(kind, tuple(children))
    object_keys(data, {"kind", "operator", "left", "right"}, {"modifiers"}, path)
    if kind != "comparison":
        raise QueryError("Unknown expression kind", path)
    left = _value(data["left"], path + "/left")
    right = _value(data["right"], path + "/right")
    values = (left, *right.value) if right.kind == "list" else (left, right)
    type_name = next((value_type(value) for value in values if value.kind != "null"), "text")
    definition = TYPES[type_name]
    operator = data["operator"]
    if operator not in definition.operators:
        raise QueryError(f"Operator is not supported for {type_name}", path + "/operator")
    if operator == "in":
        if left.kind == "list" or right.kind != "list":
            raise QueryError("IN requires a scalar value and a list", path)
        others = right.value
    else:
        others = (left, right)
    budget[1] += len(others) if operator == "in" else 1
    if budget[1] > 256:
        raise QueryError(
            "Query exceeds 256 comparisons after expanding IN lists", path, "complexity"
        )
    if any(value_type(value) not in (type_name, "null") for value in others):
        raise QueryError("Incompatible operand types", path, "type")
    if type_name == "time" and any(
        value.kind == "time" and dt.time.fromisoformat(value.value).tzinfo is not None
        for value in others
    ):
        raise QueryError("A local time field cannot be compared with an offset time", path, "type")
    if operator not in ("equals", "in") and any(v.kind == "null" for v in others):
        raise QueryError("NULL is only valid with equality or IN", path)
    if operator != "like" and any(v.kind == "pattern" for v in others):
        raise QueryError("Pattern literals require LIKE", path)
    if operator in TEXT_OPERATORS:
        if left.kind not in ("field", "string") or right.kind not in ("string", "pattern"):
            raise QueryError("Text matching requires text and a literal", path)
        if operator in ("fuzzy", "phrase") and not right.value.strip():
            raise QueryError("Text matching requires nonempty search text", path)
        if operator == "fuzzy" and len(right.value) > 128:
            raise QueryError("Fuzzy search text must be at most 128 characters", path + "/right")
        if operator == "like":
            pattern_parts(right.value, path + "/right/value")
            right = Value("pattern", right.value)
    if left.kind == right.kind == "field":
        if FIELDS[left.value].many or FIELDS[right.value].many:
            raise QueryError("Field-to-field comparisons require scalar fields", path)
        if not set(FIELDS[left.value].categories) & set(FIELDS[right.value].categories):
            raise QueryError("Fields do not apply to any common result category", path)
    if right.kind == "field" and FIELDS[right.value].many:
        if operator != "equals":
            raise QueryError("A multi-valued field must be the left operand", path)
        left, right = right, left
    modifiers = data.get("modifiers", {})
    object_keys(modifiers, set(), {"nulls", "case", "accent"}, path + "/modifiers")
    nulls = modifiers.get("nulls", "distinct")
    case = modifiers.get("case", "sensitive" if operator == "like" else "insensitive")
    accent = modifiers.get("accent", "insensitive")
    if nulls not in ("distinct", "as_empty"):
        raise QueryError("Unknown null policy", path + "/modifiers/nulls")
    if case not in ("sensitive", "insensitive") or accent not in ("sensitive", "insensitive"):
        raise QueryError("Unknown sensitivity modifier", path + "/modifiers")
    if not definition.textual and ({"case", "accent"} & modifiers.keys()):
        raise QueryError("Sensitivity modifiers require text", path + "/modifiers")
    if nulls == "as_empty" and definition.empty is None:
        raise QueryError(f"{type_name} has no empty value", path + "/modifiers/nulls")
    return Comparison(operator, left, right, type_name, Options(nulls, case, accent))


def pattern_parts(value: str, path: str = "") -> tuple[tuple[str, str], ...]:
    result = []
    literal = []
    escaped = False
    wildcards = 0
    for char in value:
        if not escaped and char == "\\":
            escaped = True
            continue
        if not escaped and char in "*?":
            if literal:
                result.append(("literal", "".join(literal)))
                literal = []
            result.append(("wildcard", "%" if char == "*" else "_"))
            wildcards += 1
        else:
            literal.append(char)
        escaped = False
    if escaped:
        raise QueryError("A pattern cannot end with an unescaped backslash", path)
    if wildcards > 64:
        raise QueryError("A pattern may contain at most 64 wildcards", path, "complexity")
    if literal or not result:
        result.append(("literal", "".join(literal)))
    return tuple(result)


def decode_query(data) -> Query:
    object_keys(data, {"version", "where"}, {"orderBy"}, "")
    if type(data["version"]) is not int or data["version"] != 1:
        raise QueryError("Only query version 1 is supported", "/version")
    where = _expression(data["where"], "/where", 0, [0, 0])
    order = []
    raw_order = data.get(
        "orderBy",
        [{"expression": {"kind": "field", "name": "relevance"}, "direction": "descending"}],
    )
    if not isinstance(raw_order, list) or not 1 <= len(raw_order) <= 5:
        raise QueryError("Specify between 1 and 5 sort fields", "/orderBy")
    for i, item in enumerate(raw_order):
        path = f"/orderBy/{i}"
        object_keys(item, {"expression", "direction"}, set(), path)
        expression = item["expression"]
        object_keys(expression, {"kind", "name"}, set(), path + "/expression")
        name = expression["name"]
        if (
            expression["kind"] != "field"
            or not isinstance(name, str)
            or name not in FIELDS
            or not FIELDS[name].sortable
        ):
            raise QueryError("Expected a sortable field", path + "/expression")
        if item["direction"] not in ("ascending", "descending"):
            raise QueryError("Invalid sort direction", path + "/direction")
        if name in {entry.field for entry in order}:
            raise QueryError("Duplicate sort field", path)
        order.append(Order(name, item["direction"]))
    return Query(where, tuple(order))


def encode_value(value):
    if value.kind == "null":
        return {"kind": "null"}
    if value.kind in ("field", "temporal_sentinel"):
        return {"kind": value.kind, "name": value.value}
    return {
        "kind": value.kind,
        "value": (
            [encode_value(item) for item in value.value] if value.kind == "list" else value.value
        ),
    }


def encode_expression(node):
    if isinstance(node, Group):
        return {"kind": node.kind, "operands": [encode_expression(n) for n in node.operands]}
    if isinstance(node, Negation):
        return {"kind": "not", "operand": encode_expression(node.operand)}
    modifiers = {"nulls": node.options.nulls}
    if TYPES[node.type].textual:
        modifiers.update(case=node.options.case, accent=node.options.accent)
    return {
        "kind": "comparison",
        "operator": node.operator,
        "left": encode_value(node.left),
        "right": encode_value(node.right),
        "modifiers": modifiers,
    }


def encode_query(query: Query) -> dict:
    return {
        "version": 1,
        "where": encode_expression(query.where),
        "orderBy": [
            {"expression": {"kind": "field", "name": order.field}, "direction": order.direction}
            for order in query.order
        ],
    }


def search_schema() -> dict:
    return {
        "version": 1,
        "resultTypes": list(RESULT_TYPES),
        "types": {
            name: {
                "operators": list(t.operators),
                "emptyValue": t.empty,
                "supportsEmpty": t.empty is not None,
                "modifiers": ["nulls", *(["case", "accent"] if t.textual else [])],
                "defaults": {
                    "nulls": "distinct",
                    **({"case": "insensitive", "accent": "insensitive"} if t.textual else {}),
                },
                "operatorDefaults": {"like": {"case": "sensitive"}} if t.textual else {},
            }
            for name, t in TYPES.items()
        },
        "fields": {
            name: {
                "type": f.type,
                "resultTypes": list(f.categories),
                "multiple": f.many,
                "sortable": f.sortable,
            }
            for name, f in FIELDS.items()
        },
    }
