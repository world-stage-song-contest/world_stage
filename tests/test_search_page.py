from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.search.query import RESULT_TYPES


def test_search_page_uses_the_api_query_rules_and_keeps_the_input(client):
    @settings(max_examples=24, deadline=None)
    @given(
        year=st.integers(2000, 2050),
        offset=st.sampled_from([0, 50, 10000]),
        result_type=st.sampled_from(RESULT_TYPES),
        enhanced=st.booleans(),
    )
    def check(year, offset, result_type, enhanced):
        condition = f'year >= {year} OR name CONTAINS "a"'
        query = f"-- Keep this comment.\n{condition} ORDER BY year -- Keep this too."
        response = client.post(
            "/search",
            data={"q": query, "offset": str(offset), "type": result_type},
            headers={"Accept": "application/json", "X-Search-Fragment": "1" if enhanced else "0"},
        )
        api_response = client.post(
            "/api/search",
            json={
                "query": f'type = "{result_type}" AND ({condition}) ORDER BY year',
                "page": {"limit": 50, "offset": offset},
            },
        )
        assert response.status_code == api_response.status_code == 200
        page = response.get_json()
        api_result = api_response.get_json()["result"]
        assert page["query_text"] == query
        assert page["result_type"] == result_type
        assert all(item["type"] == result_type for item in page["result"]["results"])
        assert page["error"] is None
        assert page["previous_offset"] == (max(0, offset - 50) if offset else None)
        for key in ("query", "results", "page"):
            assert page["result"][key] == api_result[key]
        following = api_result["page"]["nextOffset"]
        assert page["next_offset"] == (
            following if following is not None and following <= 10000 else None
        )

    check()


def test_search_page_preserves_invalid_input_and_reports_errors(client):
    @given(
        query=st.sampled_from(["", " \n", "title = 4", 'title = "x" AND', "year >= 2000"]),
        offset=st.sampled_from(["0", "-1", "10001", "abc"]),
        result_type=st.one_of(st.sampled_from(RESULT_TYPES), st.text(max_size=20)),
        enhanced=st.booleans(),
    )
    def check(query, offset, result_type, enhanced):
        response = client.post(
            "/search",
            data={"q": query, "offset": offset, "type": result_type},
            headers={"Accept": "application/json", "X-Search-Fragment": "1" if enhanced else "0"},
        )
        valid = query == "year >= 2000" and offset == "0" and result_type in RESULT_TYPES
        assert response.status_code == (200 if valid else 400)
        page = response.get_json()
        assert page["query_text"] == query
        assert page["result_type"] == result_type
        assert bool(page["error"]) != valid
        if not valid:
            assert page["result"] is None
            assert page["next_offset"] is None

    check()
