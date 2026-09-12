import datetime as dt
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage import create_app
from world_stage.search import (
    QueryError,
    convert_query,
    decode_query,
    encode_query,
    format_wsql,
    parse_wsql,
)
from world_stage.search.query import FIELDS, TYPES
from world_stage.search.wsql import WsqlError

text_values = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0"), max_size=32
)


def comparison(name, operator, value, modifiers=None):
    return {
        "kind": "comparison",
        "operator": operator,
        "left": {"kind": "field", "name": name},
        "right": value,
        "modifiers": modifiers or {},
    }


def document(node):
    return {"version": 1, "where": node}


@st.composite
def literal(draw, type_name):
    if type_name == "text":
        return {"kind": "string", "value": draw(text_values)}
    if type_name == "number":
        return {"kind": "number", "value": draw(st.integers(-10000, 10000))}
    if type_name == "boolean":
        return {"kind": "boolean", "value": draw(st.booleans())}
    if type_name == "date":
        value = draw(st.dates(min_value=dt.date(1900, 1, 1), max_value=dt.date(2100, 1, 1)))
    elif type_name == "time":
        value = draw(st.times()).replace(microsecond=0)
    else:
        value = draw(
            st.datetimes(min_value=dt.datetime(1900, 1, 1), max_value=dt.datetime(2100, 1, 1))
        ).replace(microsecond=0)
    return {"kind": type_name, "value": value.isoformat()}


@st.composite
def comparisons(draw):
    name = draw(st.sampled_from([name for name in FIELDS if name != "relevance"]))
    type_name = FIELDS[name].type
    operator = draw(st.sampled_from(TYPES[type_name].operators))
    modifiers = {}
    if TYPES[type_name].empty is not None:
        modifiers["nulls"] = draw(st.sampled_from(["distinct", "as_empty"]))
    if type_name == "text":
        modifiers["case"] = draw(st.sampled_from(["sensitive", "insensitive"]))
        modifiers["accent"] = draw(st.sampled_from(["sensitive", "insensitive"]))
    if operator == "in":
        value = {
            "kind": "list",
            "value": draw(
                st.lists(
                    st.one_of(literal(type_name), st.just({"kind": "null"})), min_size=1, max_size=4
                )
            ),
        }
    elif operator == "equals":
        value = draw(st.one_of(literal(type_name), st.just({"kind": "null"})))
    elif operator in ("phrase", "fuzzy", "like"):
        value = {
            "kind": "string",
            "value": draw(st.text(alphabet="abé愛🎵*?\"'", min_size=1, max_size=20)),
        }
    else:
        value = draw(literal(type_name))
    return comparison(name, operator, value, modifiers)


expressions = st.recursive(
    comparisons(),
    lambda child: st.one_of(
        child.map(lambda node: {"kind": "not", "operand": node}),
        st.tuples(st.sampled_from(["and", "or"]), st.lists(child, min_size=1, max_size=3)).map(
            lambda pair: {"kind": pair[0], "operands": pair[1]}
        ),
    ),
    max_leaves=10,
)


@settings(max_examples=200)
@given(
    node=expressions,
    ordering=st.lists(
        st.tuples(
            st.sampled_from([name for name, field in FIELDS.items() if field.sortable]),
            st.sampled_from(["ascending", "descending"]),
        ),
        min_size=1,
        max_size=5,
        unique_by=lambda pair: pair[0],
    ),
)
def test_conversion_preserves_typed_queries_and_canonicalization_is_idempotent(node, ordering):
    source = document(node) | {
        "orderBy": [
            {"expression": {"kind": "field", "name": name}, "direction": direction}
            for name, direction in ordering
        ]
    }
    query = decode_query(source)
    wsql = convert_query(source, "wsql")
    assert parse_wsql(wsql) == query
    assert convert_query(wsql, "json") == encode_query(query)
    assert convert_query(wsql, "wsql") == wsql


@given(value=text_values, single=st.booleans(), escape=st.sampled_from(["json", "long", "braces"]))
def test_unicode_string_spellings_decode_to_the_same_value(value, single, escape):
    if escape == "json":
        content = json.dumps(value, ensure_ascii=True)[1:-1]
    elif escape == "long":
        content = "".join(f"\\U{ord(char):08x}" for char in value)
    else:
        content = "".join(f"\\u{{{ord(char):x}}}" for char in value)
    quote = "'" if single else '"'
    if single:
        content = content.replace("'", "\\'")
    source = "title = " + quote + content + quote
    query = parse_wsql(source)
    expected = decode_query(
        document(comparison("title", "equals", {"kind": "string", "value": value}))
    )
    assert query == expected
    assert parse_wsql(format_wsql(query)) == query


@given(value=text_values, level=st.integers(0, 64), ending=st.sampled_from(["\n", "\r", "\r\n"]))
def test_long_strings_preserve_values_and_share_string_escapes(value, level, ending):
    value = ending + value + "[]='\"\\--[[" + ending
    body = "".join(
        "\\" + char
        if char in "[]='\"\\"
        else char
        if char in "\r\n"
        else json.dumps(char, ensure_ascii=True)[1:-1]
        for char in value
    )
    equals = "=" * level
    source = "title = [" + equals + "[" + body + "]" + equals + "]"
    expected = decode_query(
        document(comparison("title", "equals", {"kind": "string", "value": value}))
    )
    assert parse_wsql(source) == expected
    assert parse_wsql(format_wsql(expected)) == expected
    quoted = "title = " + json.dumps(value).replace("[", "\\[").replace("]", "\\]").replace(
        "=", "\\="
    )
    assert parse_wsql(quoted) == expected


@given(
    blocked_count=st.integers(0, 65),
    partial_end=st.booleans(),
    multiline=st.booleans(),
)
def test_canonical_strings_use_the_shortest_safe_marker_or_escaped_quotes(
    blocked_count, partial_end, multiline
):
    value = "'\"" + ("\n" if multiline else "")
    value += "|".join("]" + "=" * level + "]" for level in range(blocked_count))
    if partial_end:
        value += "]" + "=" * blocked_count
    query = decode_query(
        document(comparison("title", "equals", {"kind": "string", "value": value}))
    )
    formatted = format_wsql(query)
    level = blocked_count + int(partial_end)
    if level <= 64:
        assert formatted.startswith("title = [" + "=" * level + "[")
    else:
        assert formatted.startswith('title = "')
        assert "\n" not in formatted
    assert parse_wsql(formatted) == query
    assert format_wsql(parse_wsql(formatted)) == formatted


@given(
    word=st.text(alphabet="abc", max_size=20),
    quote=st.sampled_from(["'", '"']),
    ending=st.sampled_from(["\n", "\r", "\r\n"]),
    level=st.integers(0, 64),
    excess=st.integers(1, 100),
)
def test_strings_reject_raw_quoted_newlines_and_invalid_long_markers(
    word, quote, ending, level, excess
):
    opening = "[" + "=" * level + "["
    for source in (
        "title = " + quote + word + ending + quote,
        "title = " + quote + word + "\\" + ending + quote,
        "title = " + opening + word,
        "title = " + opening + word + "]" + "=" * (level + excess) + "]",
    ):
        with pytest.raises(WsqlError):
            parse_wsql(source)
    equals = "=" * (64 + excess)
    with pytest.raises(WsqlError) as error:
        parse_wsql("title = [" + equals + "[" + word + "]" + equals + "]")
    assert error.value.code == "complexity"


@given(
    word=st.text(alphabet="abcé*?", min_size=1, max_size=20),
    insensitive=st.booleans(),
    override=st.sampled_from([None, "CASE", "NO CASE"]),
    negative=st.booleans(),
    accent=st.booleans(),
)
def test_ilike_and_negative_aliases_share_modifiers(word, insensitive, override, negative, accent):
    operator = "ILIKE" if insensitive else "LIKE"
    modifiers = {"case": "insensitive" if insensitive else "sensitive"}
    clauses = []
    if override is not None:
        clauses.append(override)
        modifiers["case"] = "sensitive" if override == "CASE" else "insensitive"
    clauses.append("ACCENT" if accent else "NO ACCENT")
    modifiers["accent"] = "sensitive" if accent else "insensitive"
    source = f"title {'NOT ' if negative else ''}{operator} {json.dumps(word)}"
    source += " WITH " + ", ".join(clauses)
    expected = comparison("title", "like", {"kind": "string", "value": word}, modifiers)
    if negative:
        expected = {"kind": "not", "operand": expected}
    query = parse_wsql(source)
    assert query == decode_query(document(expected))
    assert parse_wsql(format_wsql(query)) == query


@given(
    name=st.sampled_from(["native_title", "duration", "has_video", "genre"]),
    empty=st.booleans(),
    negative=st.booleans(),
)
def test_missingness_aliases_preserve_type_defaults(name, empty, negative):
    expected = comparison(
        name, "equals", {"kind": "null"}, {"nulls": "as_empty" if empty else "distinct"}
    )
    if negative:
        expected = {"kind": "not", "operand": expected}
    source = f"{name} IS {'NOT ' if negative else ''}{'EMPTY' if empty else 'NULL'}"
    assert parse_wsql(source) == decode_query(document(expected))
    for operator in ("!=", "<>") if negative else ("=",):
        alternative = f"{name} {operator} NULL" + (" WITH NULLS AS EMPTY" if empty else "")
        assert parse_wsql(source) == parse_wsql(alternative)


@given(
    bounds=st.tuples(st.integers(-100, 100), st.integers(-100, 100)),
    operators=st.tuples(
        st.sampled_from(["<", "<=", ">", ">="]), st.sampled_from(["<", "<=", ">", ">="])
    ),
    empty=st.booleans(),
)
def test_comparison_chains_expand_with_shared_modifiers(bounds, operators, empty):
    left, right = bounds
    first, second = operators
    modifier = " WITH NULLS AS EMPTY" if empty else ""
    chain = f"{left} {first} year {second} {right}{modifier}"
    expanded = f"{left} {first} year{modifier} AND year {second} {right}{modifier}"
    assert parse_wsql(chain) == parse_wsql(expanded)
    assert parse_wsql(format_wsql(parse_wsql(expanded))) == parse_wsql(chain)


@given(case=st.booleans(), accent=st.booleans())
def test_equality_chains_expand_without_changing_sensitivity(case, accent):
    modifier = f" WITH {'CASE' if case else 'NO CASE'}, {'ACCENT' if accent else 'NO ACCENT'}"
    assert parse_wsql("title = native_title = name" + modifier) == parse_wsql(
        "title = native_title" + modifier + " AND native_title = name" + modifier
    )


@given(
    day=st.dates(min_value=dt.date(1900, 1, 1), max_value=dt.date(2100, 1, 1)),
    hour=st.integers(0, 23),
    offset=st.integers(-12, 14),
    sentinel=st.sampled_from(["NOW", "TODAY", "TOMORROW", "YESTERDAY"]),
)
def test_temporal_syntax_preserves_values_and_unresolved_sentinels(day, hour, offset, sentinel):
    instant = dt.datetime.combine(day, dt.time(hour), dt.timezone(dt.timedelta(hours=offset)))
    query = parse_wsql(f"starts_at = @{instant.isoformat()}")
    assert parse_wsql(format_wsql(query)) == query
    assert dt.datetime.fromisoformat(query.where.right.value) == instant
    name = "voting_opens" if sentinel == "NOW" else "date"
    query = parse_wsql(f"{name} = {sentinel.lower()}")
    assert query.where.right.kind == "temporal_sentinel"
    assert parse_wsql(format_wsql(query)) == query


@given(source=st.text(max_size=300))
def test_arbitrary_wsql_is_rejected_cleanly_or_round_trips(source):
    try:
        query = parse_wsql(source)
    except QueryError:
        return
    assert parse_wsql(format_wsql(query)) == query


@given(
    comment=st.text(alphabet=st.characters(blacklist_characters="\r\n"), max_size=60),
    ending=st.sampled_from(["\n", "\r\n", "\r"]),
    word=text_values,
    year=st.integers(-1000, 2050),
    day=st.dates(),
    level=st.one_of(st.none(), st.integers(0, 64)),
)
def test_comments_between_tokens_are_discarded_without_changing_values(
    comment, ending, word, year, day, level
):
    if level is None:
        separator = "-- " + comment + ending
        trailing = "-- " + comment
        marker = "--"
    else:
        marker = "--[" + "=" * level + "["
        closing = "--]" + "=" * level + "]"
        other = "=" * ((level + 1) % 65)
        body = comment.replace(closing, " ")
        body += ending + "--[" + other + "[ unmatched --]" + other + "]" + ending
        separator = marker + body + closing
        trailing = separator
    for tokens in (
        ["date", "=", "@" + day.isoformat()],
        ["starts_at", ">=", "@" + day.isoformat() + "T12:30-03:30"],
        [
            "year",
            ">=",
            str(year),
            "AND",
            "title",
            "NOT",
            "CONTAINS",
            json.dumps(marker + word),
            "WITH",
            "CASE",
            ",",
            "ACCENT",
        ],
        ["title", "=", "'" + json.dumps(marker + word)[1:-1].replace("'", "\\'") + "'"],
    ):
        source = " ".join(tokens)
        commented = separator + separator.join(tokens) + trailing
        assert parse_wsql(commented) == parse_wsql(source)
        for target in ("json", "wsql"):
            assert convert_query(commented, target) == convert_query(source, target)


@given(
    comments=st.lists(st.text(alphabet="abc --'\"\\", max_size=30), min_size=1, max_size=10),
    ending=st.sampled_from(["\n", "\r\n", "\r"]),
)
def test_comments_preserve_original_error_locations(comments, ending):
    prefix = "".join("--" + comment + ending for comment in comments)
    with pytest.raises(WsqlError) as error:
        parse_wsql(prefix + "year = ?")
    assert error.value.location["offset"] == len(prefix) + len("year = ")
    assert error.value.location["line"] == len(comments) + 1
    assert error.value.location["column"] == len("year = ") + 1


@given(
    level=st.integers(0, 64),
    excess=st.integers(1, 100),
    prefix_lines=st.integers(0, 10),
)
def test_block_comments_require_a_matching_bounded_delimiter(level, excess, prefix_lines):
    prefix = "\n" * prefix_lines + "year = 2026 "
    opening = "--[" + "=" * level + "["
    closing = "--]" + "=" * level + "]"
    wrong_closing = "--]" + "=" * (level + excess) + "]"
    for suffix in ("", wrong_closing, "]" + "=" * level + "]"):
        with pytest.raises(WsqlError) as error:
            parse_wsql(prefix + opening + "comment\n" + suffix)
        assert error.value.location["offset"] == len(prefix)
        assert error.value.location["line"] == prefix_lines + 1
    too_long = "=" * (64 + excess)
    with pytest.raises(WsqlError) as error:
        parse_wsql(prefix + "--[" + too_long + "[comment--]" + too_long + "]")
    assert error.value.code == "complexity"
    assert error.value.location["offset"] == len(prefix)
    assert parse_wsql(prefix + opening + opening + closing) == parse_wsql(prefix)


@given(
    prefix=st.text(alphabet=" \t\n", max_size=20),
    suffix=st.sampled_from(
        [
            "year = 1 < 2",
            "year != 1 = 2",
            "year < 1 <> 2",
            'title = "x" WITH CASE, NO CASE',
            'title = "\\uD800"',
            'title = "\\q"',
            'date = "2026-01-01"',
            'title = "unterminated',
            "year = 1 ORDER BY year DESC ASC",
            'title = "x"; DROP TABLE song',
        ]
    ),
)
def test_invalid_syntax_and_types_report_source_locations(prefix, suffix):
    source = prefix + suffix
    with pytest.raises(WsqlError) as error:
        parse_wsql(source)
    location = error.value.location
    assert 0 <= location["offset"] <= len(source)
    assert location["line"] == source.count("\n", 0, location["offset"]) + 1
    assert location["column"] == location["offset"] - source.rfind("\n", 0, location["offset"])


def test_conversion_endpoint_works_without_a_database():
    app = create_app({"TESTING": True, "LOCAL_ASSETS": True})
    client = app.test_client()

    @settings(max_examples=30, deadline=None)
    @given(node=expressions)
    def check(node):
        source = document(node)
        response = client.post("/api/search/convert", json={"query": source, "to": "wsql"})
        assert response.status_code == 200
        wsql = response.get_json()["result"]["query"]
        response = client.post("/api/search/convert", json={"query": wsql, "to": "json"})
        assert response.status_code == 200
        assert response.get_json()["result"]["query"] == encode_query(decode_query(source))

    check()


def test_api_rejects_malformed_queries_before_database_access():
    app = create_app({"TESTING": True, "LOCAL_ASSETS": True})
    client = app.test_client()

    @given(
        value=st.integers(-1000, 1000),
        path=st.sampled_from(["/api/search", "/api/search/convert"]),
        failure=st.sampled_from(["syntax", "type", "size"]),
    )
    def check(value, path, failure):
        if failure == "syntax":
            source = f"year = {value} AND"
        elif failure == "type":
            source = f"title = {value}"
        else:
            source = 'title = "' + "x" * 65536 + '"'
        body = {"query": source}
        if path.endswith("convert"):
            body["to"] = "json"
        response = client.post(path, json=body)
        assert response.status_code == (413 if failure == "size" else 400)
        if failure != "size":
            location = response.get_json()["error"]["location"]
            assert 0 <= location["offset"] <= len(source)

    check()


@given(
    depth=st.integers(65, 300),
    width=st.integers(101, 300),
    number_length=st.integers(4500, 5000),
)
def test_excessive_queries_fail_with_query_errors(depth, width, number_length):
    for source in (
        "(" * depth + "year = 2026" + ")" * depth,
        " OR ".join(["year = 2026"] * width),
        "year = " + "9" * number_length,
    ):
        with pytest.raises(QueryError):
            parse_wsql(source)


def test_search_api_executes_both_representations_identically(client):
    @settings(max_examples=15, deadline=None)
    @given(year=st.integers(2000, 2050), negative=st.booleans(), limit=st.integers(1, 5))
    def check(year, negative, limit):
        source = f"year {'!=' if negative else '='} {year} ORDER BY year DESC"
        responses = [
            client.post("/api/search", json={"query": value, "page": {"limit": limit}})
            for value in (source, convert_query(source, "json"))
        ]
        assert all(response.status_code == 200 for response in responses)
        left, right = [response.get_json()["result"] for response in responses]
        for key in ("query", "results", "page"):
            assert left[key] == right[key]

    check()
