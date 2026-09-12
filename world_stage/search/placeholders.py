import json

from .query import TYPES, QueryError, Value, _value, decode_query, value_type
from .wsql import Lexer, Parser, format_value, parse_wsql

PARAMETER_TYPES = ("text", "integer", "number", "boolean", "date", "time", "datetime")
SAMPLES = {
    "text": {"kind": "string", "value": "sample"},
    "number": {"kind": "number", "value": 1},
    "boolean": {"kind": "boolean", "value": True},
    "date": {"kind": "date", "value": "2026-01-01"},
    "time": {"kind": "time", "value": "12:00"},
    "datetime": {"kind": "datetime", "value": "2026-01-01T12:00Z"},
}


def base_type(type_name):
    return "number" if type_name == "integer" else type_name


def replace_values(node, replacements):
    if isinstance(node, list):
        return [replace_values(item, replacements) for item in node]
    if not isinstance(node, dict):
        return node
    if node.get("kind") == "placeholder":
        return replacements[node["name"]]
    return {key: replace_values(value, replacements) for key, value in node.items()}


def placeholder_names(node):
    if isinstance(node, list):
        return set().union(*(placeholder_names(item) for item in node))
    if not isinstance(node, dict):
        return set()
    if node.get("kind") == "placeholder":
        return {node["name"]}
    return set().union(*(placeholder_names(value) for value in node.values()))


def comparisons(node):
    if node["kind"] == "comparison":
        yield node
    elif node["kind"] == "not":
        yield from comparisons(node["operand"])
    else:
        for child in node["operands"]:
            yield from comparisons(child)


def analyze_placeholders(source, overrides=None, *, require_types=False):
    if not isinstance(source, str) or len(source) > 65536:
        raise QueryError("WSQL must be a string of at most 65536 characters")
    document = Parser(source).document()
    names = placeholder_names(document["where"])
    if placeholder_names(document.get("orderBy", [])):
        raise QueryError("A placeholder is a value, not a sort field")
    if len(names) > 32 or any(len(name) > 64 for name in names):
        raise QueryError("Use at most 32 placeholders with names of at most 64 characters")
    overrides = overrides if overrides is not None else {}
    if not isinstance(overrides, dict) or set(overrides) - names:
        raise QueryError("Unknown placeholder type override")
    if any(value not in PARAMETER_TYPES for value in overrides.values()):
        raise QueryError("Unknown placeholder type")
    possible = {name: set(TYPES) for name in names}
    related = []
    for comparison in comparisons(document["where"]):
        used = placeholder_names(comparison)
        if not used:
            decode_query({"version": 1, "where": comparison})
            continue
        candidates = set()
        for type_name, sample in SAMPLES.items():
            concrete = replace_values(comparison, dict.fromkeys(used, sample))
            try:
                decode_query({"version": 1, "where": concrete})
            except QueryError:
                continue
            candidates.add(type_name)
        for name in used:
            possible[name] &= candidates
        related.append(used)

    def propagate():
        changed = True
        while changed:
            changed = False
            for group in related:
                common = set.intersection(*(possible[name] for name in group))
                for name in group:
                    if possible[name] != common:
                        possible[name] = common.copy()
                        changed = True

    propagate()
    inferred = {
        name: next(iter(types)) if len(types) == 1 else None for name, types in possible.items()
    }
    for name, type_name in overrides.items():
        possible[name] &= {base_type(type_name)}
    propagate()
    parameters = []
    replacements = {}
    for name in sorted(names):
        options = possible[name]
        if not options:
            raise QueryError(f"Placeholder ${name} has incompatible types or operators")
        type_name = overrides.get(name) or (next(iter(options)) if len(options) == 1 else None)
        if require_types and type_name is None:
            raise QueryError(f"Choose a type for ${name}")
        parameters.append({"name": name, "type": type_name, "inferredType": inferred[name]})
        replacements[name] = (
            SAMPLES[base_type(type_name)] if type_name else SAMPLES[sorted(options)[0]]
        )
    decode_query(replace_values(document, replacements))
    return parameters


def parameter_value(type_name, value):
    if type_name not in PARAMETER_TYPES:
        raise QueryError("Unknown placeholder type")
    if value is None:
        return Value("null")
    if type_name == "integer" and (type(value) is not int):
        raise QueryError("Enter a whole number")
    kind = "string" if type_name == "text" else base_type(type_name)
    if kind == "date" and value in ("TODAY", "TOMORROW", "YESTERDAY"):
        return Value("temporal_sentinel", value.lower())
    if kind == "datetime" and value == "NOW":
        return Value("temporal_sentinel", "now")
    result = _value({"kind": kind, "value": value}, "/parameters")
    if value_type(result) != base_type(type_name):
        raise QueryError("The value does not match the placeholder type")
    return result


def input_value(type_name, text):
    if type_name == "text":
        return text
    text = text.strip()
    if text.upper() == "NULL":
        return None
    if type_name in ("integer", "number", "boolean"):
        try:
            value = json.loads(text.lower())
        except (ValueError, RecursionError) as exc:
            raise QueryError(f"Enter a valid {type_name} value") from exc
    else:
        value = text.removeprefix("@")
    parameter_value(type_name, value)
    return value


def prepare_parameters(source, settings):
    if not isinstance(settings, dict) or any(
        not isinstance(item, dict) for item in settings.values()
    ):
        raise QueryError("Expected placeholder settings")
    if any(set(item) - {"type", "default"} for item in settings.values()):
        raise QueryError("Unknown placeholder setting")
    parameters = analyze_placeholders(
        source, {name: item.get("type") for name, item in settings.items()}, require_types=True
    )
    normalized = {}
    for parameter in parameters:
        name, type_name = parameter["name"], parameter["type"]
        normalized[name] = {"type": type_name}
        if "default" in settings.get(name, {}):
            value = settings[name]["default"]
            parameter_value(type_name, value)
            normalized[name]["default"] = value
    samples = {
        name: definition.get("default", SAMPLES[base_type(definition["type"])]["value"])
        for name, definition in normalized.items()
    }
    resolve_placeholders(source, normalized, samples)
    return normalized


def resolve_placeholders(source, settings, values=None):
    values = values if values is not None else {}
    if not isinstance(values, dict) or set(values) - settings.keys():
        raise QueryError("Unknown placeholder value")
    analyze_placeholders(
        source, {name: item["type"] for name, item in settings.items()}, require_types=True
    )
    replacements = {}
    missing = []
    for name, definition in settings.items():
        if name not in values and "default" not in definition:
            missing.append({"name": name, "type": definition["type"]})
            continue
        value = values[name] if name in values else definition["default"]
        replacements[name] = format_value(parameter_value(definition["type"], value))
    if missing:
        return {"missing": missing}
    lexer = Lexer(source)
    parts = []
    end = 0
    while (token := lexer.next()).kind != "end":
        if token.kind == "placeholder":
            parts.extend((source[end : token.start], replacements[token.value[1:]]))
            end = token.end
    parts.append(source[end:])
    resolved = "".join(parts)
    parse_wsql(resolved)
    return {"query": resolved, "missing": []}
