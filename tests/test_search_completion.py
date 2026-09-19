import json
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.search import parse_wsql, search_schema
from world_stage.search.query import FIELDS, RESULT_TYPES, Value
from world_stage.search.wsql import format_value


@pytest.fixture(scope="module")
def complete():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to test browser query completion.")

    def run(source, result_type, caret=None, choices=None):
        script = """
            import {completeWsql} from './world_stage/static/js/wsql-completion.js';
            let input = '';
            for await (const chunk of process.stdin) input += chunk;
            const {source, resultType, caret, schema, choices} = JSON.parse(input);
            console.log(JSON.stringify(completeWsql(source, caret, schema, resultType, choices)));
        """
        result = subprocess.run(
            [node, "--input-type=module", "-e", script],
            input=json.dumps(
                {
                    "source": source,
                    "resultType": result_type,
                    "caret": len(source.encode("utf-16-le")) // 2 if caret is None else caret,
                    "schema": search_schema(),
                    "choices": choices or {},
                }
            ),
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    return run


def test_field_suggestions_match_the_page_type_and_preserve_surrounding_text(complete):
    @settings(max_examples=40, deadline=None)
    @given(
        result_type=st.sampled_from(RESULT_TYPES),
        sorting=st.booleans(),
        length=st.integers(0, 20),
        uppercase=st.booleans(),
    )
    def check(result_type, sorting, length, uppercase):
        eligible = {
            name
            for name, field in FIELDS.items()
            if result_type in field.categories
            and (field.sortable if sorting else name != "relevance")
        }
        field = sorted(eligible)[length % len(eligible)]
        prefix = field[: length % (len(field) + 1)]
        if uppercase:
            prefix = prefix.upper()
        before = 'name = "x" ORDER BY ' if sorting else "-- Keep this.\nNOT ("
        after = ' = "untouched"' if not sorting else " DESC"
        source = before + prefix + after
        result = complete(source, result_type, len(before + prefix))
        assert field in result["suggestions"]
        assert set(result["suggestions"]) <= eligible | {"NOT", "WHERE"}
        assert all(item.lower().startswith(prefix.lower()) for item in result["suggestions"])
        assert source[: result["start"]] == before
        assert source[result["end"] :] == after

    check()


def test_value_suggestions_preserve_typed_values_and_surrounding_conditions(complete):
    @settings(max_examples=40, deadline=None)
    @given(
        value=st.text(
            st.characters(blacklist_categories=("Cs",), blacklist_characters="\0"),
            min_size=1,
            max_size=35,
        ),
        cut=st.integers(0, 35),
        in_list=st.booleans(),
        quote=st.sampled_from(['"', "'", "[=["]),
    )
    def check(value, cut, in_list, quote):
        closing = "]=]" if quote == "[=[" else quote

        def escape(text):
            return "".join(
                json.dumps(char, ensure_ascii=False)[1:-1].replace("'", "\\'").replace("]", "\\]")
                for char in text
            )

        before = 'genre IN ("Keep", ' if in_list else "genre = "
        after = (")" if in_list else "") + " AND year = 2024"
        source = before + quote + escape(value) + closing + after
        prefix = before + quote + escape(value[:cut])
        result = complete(
            source,
            "entry",
            len(prefix.encode("utf-16-le")) // 2,
            choices={"genre": [{"value": value, "literal": format_value(Value("string", value))}]},
        )
        assert result["valueField"] == "genre"
        assert result["suggestions"]
        encoded = source.encode("utf-16-le")
        substituted = (
            encoded[: result["start"] * 2].decode("utf-16-le")
            + result["suggestions"][0]
            + encoded[result["end"] * 2 :].decode("utf-16-le")
        )
        parsed = parse_wsql(substituted).where.operands
        assert parsed[1].right.value == 2024
        if in_list:
            assert [item.value for item in parsed[0].right.value] == ["Keep", value]
        else:
            assert parsed[0].right.value == value

    check()


def test_all_reference_values_are_offered_before_filtering(complete):
    @settings(max_examples=20, deadline=None)
    @given(
        values=st.lists(
            st.text(alphabet="abcXYZé星 ", min_size=1, max_size=12),
            min_size=1,
            max_size=80,
            unique=True,
        ),
        operator=st.sampled_from(["=", "!=", "<>", "IN (", "NOT IN (", "CONTAINS"]),
    )
    def check(values, operator):
        options = [
            {"value": value, "literal": format_value(Value("string", value))} for value in values
        ]
        result = complete(f"genre {operator} ", "entry", choices={"genre": options})
        assert {item["literal"] for item in options} <= set(result["suggestions"])

    check()


def test_operator_suggestions_produce_valid_typed_queries(complete):
    @settings(max_examples=40, deadline=None)
    @given(
        name=st.sampled_from([name for name in FIELDS if name != "relevance"]),
        number=st.integers(-1000, 1000),
    )
    def check(name, number):
        field = FIELDS[name]
        literal = {
            "text": '"text"',
            "number": str(number),
            "boolean": "TRUE",
            "date": "@2026-01-01",
            "time": "@12:00",
            "datetime": "@2026-01-01T12:00Z",
        }[field.type]
        result = complete(f"{name} ", field.categories[0])
        assert result["suggestions"]
        for operator in result["suggestions"]:
            value = (
                ""
                if operator.startswith("IS ")
                else (f" ({literal})" if operator.endswith("IN") else f" {literal}")
            )
            parse_wsql(f"{name} {operator}{value}")

    check()


def test_completion_does_not_interfere_with_strings_or_comments(complete):
    @settings(max_examples=40, deadline=None)
    @given(
        content=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60),
        equals=st.integers(0, 64),
        kind=st.sampled_from(["single", "double", "long", "line", "block"]),
        result_type=st.sampled_from(RESULT_TYPES),
    )
    def check(content, equals, kind, result_type):
        content = content.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
        marker = "=" * equals
        closing = "]" + marker + "]"
        if kind in ("single", "double"):
            quote = "'" if kind == "single" else '"'
            source = "name = " + quote + content.replace(quote, "\\" + quote)
        elif kind == "long":
            source = "name = [" + marker + "[" + content.replace(closing, "\\]" + marker + "]")
        elif kind == "block":
            source = "--[" + marker + "[" + content.replace("--" + closing, "comment")
        else:
            source = "--" + content
        assert complete(source, result_type)["suggestions"] == []

    check()
