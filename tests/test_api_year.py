from hypothesis import given
from hypothesis import strategies as st


def test_year_index_matches_the_requested_status_filter(client, db):
    @given(status=st.sampled_from([None, "open", "closed", "ongoing", "unknown"]))
    def property_test(status):
        effective_status = status if status in {"open", "closed", "ongoing"} else None
        rows = db.execute("SELECT id, status FROM year ORDER BY id").fetchall()
        expected = [
            row["id"]
            for row in rows
            if effective_status is None or row["status"] == effective_status
        ]
        query = {} if status is None else {"type": status}

        response = client.get("/api/year", query_string=query)

        assert response.status_code == 200
        assert [year["year"] for year in response.get_json()["result"]] == expected
        assert all(
            effective_status is None or year["status"] == effective_status
            for year in response.get_json()["result"]
        )

    property_test()
