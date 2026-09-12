import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.search import parse_wsql
from world_stage.search.placeholders import (
    analyze_placeholders,
    prepare_parameters,
    resolve_placeholders,
)
from world_stage.search.query import FIELDS, QueryError

safe_text = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\0"), max_size=80
)


@given(name=st.sampled_from([name for name in FIELDS if name != "relevance"]))
def test_placeholders_infer_types_from_fields_on_either_side(name):
    for source in (f"{name} = $value", f"$value = {name}", f"{name} IN ($value, $other)"):
        parameters = analyze_placeholders(source)
        assert all(parameter["type"] == FIELDS[name].type for parameter in parameters)


@given(value=safe_text)
def test_binding_reuses_values_without_treating_them_as_query_syntax(value):
    source = 'title = $term OR name = $term OR name = "$term" -- $term\n'
    parameters = prepare_parameters(source, {"term": {"type": "text", "default": value}})
    resolved = resolve_placeholders(source, parameters)
    nodes = parse_wsql(resolved["query"]).where.operands
    assert nodes[0].right.value == nodes[1].right.value == value
    assert nodes[2].right.value == "$term"
    assert resolved["query"].endswith('OR name = "$term" -- $term\n')
    assert resolved["missing"] == []


@given(left=st.integers(-10000, 10000), right=st.integers(-10000, 10000))
def test_unknown_types_require_a_choice_and_allow_integer_overrides(left, right):
    source = "$left = $right"
    assert all(parameter["type"] is None for parameter in analyze_placeholders(source))
    with pytest.raises(QueryError):
        prepare_parameters(source, {})
    parameters = prepare_parameters(
        source,
        {
            "left": {"type": "integer", "default": left},
            "right": {"type": "integer"},
        },
    )
    assert resolve_placeholders(source, parameters)["missing"] == [
        {"name": "right", "type": "integer"}
    ]
    query = parse_wsql(resolve_placeholders(source, parameters, {"right": right})["query"])
    assert query.where.left.value == left
    assert query.where.right.value == right
    assert analyze_placeholders("year = $year", {"year": "integer"})[0]["type"] == "integer"


@given(type_name=st.sampled_from(["text", "boolean", "date", "time", "datetime"]))
def test_incompatible_manual_types_are_rejected(type_name):
    with pytest.raises(QueryError):
        analyze_placeholders("year = $value", {"value": type_name})
    with pytest.raises(QueryError):
        analyze_placeholders("year = $value AND title = $value")


def test_saved_queries_are_private_and_keep_defaults(client, db, alice_headers, bob_headers):
    @settings(max_examples=12, deadline=None)
    @given(value=safe_text, name=st.text(alphabet="abcdefghijk", min_size=1, max_size=100))
    def check(value, name):
        response = client.post(
            "/api/search/saved",
            headers=alice_headers,
            json={
                "name": name,
                "type": "entry",
                "query": "title = $term",
                "parameters": {"term": {"type": "text", "default": value}},
            },
        )
        assert response.status_code == 201
        query_id = response.get_json()["result"]["id"]
        try:
            assert any(
                item["id"] == query_id
                for item in client.get("/api/search/saved", headers=alice_headers).get_json()[
                    "result"
                ]
            )
            assert all(
                item["id"] != query_id
                for item in client.get("/api/search/saved", headers=bob_headers).get_json()[
                    "result"
                ]
            )
            assert (
                client.post(
                    f"/api/search/saved/{query_id}/resolve", headers=bob_headers, json={}
                ).status_code
                == 404
            )
            resolved = client.post(
                f"/api/search/saved/{query_id}/resolve", headers=alice_headers, json={}
            )
            assert resolved.status_code == 200
            assert parse_wsql(resolved.get_json()["result"]["query"]).where.right.value == value
            updated = {
                "name": name,
                "type": "artist",
                "query": "name = $term -- Keep this text\n",
                "parameters": {"term": {"type": "text", "default": value}},
            }
            assert (
                client.get(f"/api/search/saved/{query_id}", headers=bob_headers).status_code == 404
            )
            assert (
                client.put(
                    f"/api/search/saved/{query_id}", headers=bob_headers, json=updated
                ).status_code
                == 404
            )
            assert (
                client.put(
                    f"/api/search/saved/{query_id}", headers=alice_headers, json=updated
                ).status_code
                == 200
            )
            stored = client.get(f"/api/search/saved/{query_id}", headers=alice_headers).get_json()[
                "result"
            ]
            assert stored["query_text"] == updated["query"]
            assert stored["parameters"] == updated["parameters"]
            assert stored["result_type"] == "artist"
            assert (
                client.put(
                    f"/api/search/saved/{query_id}",
                    headers=alice_headers,
                    json={**updated, "name": name * (101 // len(name) + 1)},
                ).status_code
                == 400
            )
            assert client.get("/api/search/saved").status_code == 401
        finally:
            db.execute("DELETE FROM saved_search_query WHERE id = %s", (query_id,))
            db.commit()

    check()


def test_saved_query_form_flow_keeps_missing_values_separate_from_defaults(client, db, login):
    login(2)

    @settings(max_examples=10, deadline=None)
    @given(year=st.integers(1960, 2050), value=safe_text)
    def check(year, value):
        source = "year >= $year AND title = $title"
        headers = {"Accept": "application/json"}
        prepared = client.post("/search/save", data={"q": source, "type": "entry"}, headers=headers)
        assert prepared.status_code == 200
        assert {p["name"]: p["type"] for p in prepared.get_json()["parameters"]} == {
            "title": "text",
            "year": "number",
        }
        saved = client.post(
            "/search/save/confirm",
            headers={"X-Search-Dialog": "1"},
            data={
                "q": source,
                "type": "entry",
                "name": "Form query",
                "parameter_type.year": "integer",
                "default_enabled.year": "on",
                "default.year": str(year),
                "parameter_type.title": "text",
            },
        )
        assert saved.status_code == 201
        query_id = saved.get_json()["saved"]["id"]
        try:
            edited = client.get(f"/search/edit?saved_id={query_id}", headers=headers).get_json()
            assert edited["query_text"] == source
            assert edited["editing_saved"]["id"] == query_id
            prepared_edit = client.post(
                "/search/save",
                headers=headers,
                data={"q": source, "type": "entry", "editing_saved_id": query_id},
            ).get_json()
            assert prepared_edit["values"]["name"] == "Form query"
            assert prepared_edit["values"]["parameter_type.year"] == "integer"
            assert prepared_edit["values"]["default.year"] == str(year)
            prompted = client.get(f"/search/use?saved_id={query_id}", headers=headers)
            assert prompted.get_json()["parameters"] == [{"name": "title", "type": "text"}]
            resolved = client.post(
                "/search/use", headers=headers, data={"saved_id": query_id, "value.title": value}
            )
            assert resolved.status_code == 200
            context = resolved.get_json()
            assert context["result"] is None
            query = parse_wsql(context["query_text"])
            assert query.where.operands[0].right.value == year
            assert query.where.operands[1].right.value == value
        finally:
            db.execute("DELETE FROM saved_search_query WHERE id = %s", (query_id,))
            db.commit()

    check()
