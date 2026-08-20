from hypothesis import given
from hypothesis import strategies as st


def test_identity_discovery_matches_the_authenticated_api_principal(client, db):
    cases = [(None, None), ("token-alice", 1), ("token-bob", 2), ("token-carol", 3)]

    @given(case=st.sampled_from(cases))
    def property_test(case):
        token, user_id = case
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}

        response = client.get("/api/me", headers=headers)

        assert response.status_code == 200
        data = response.get_json()["result"]
        assert data["authenticated"] is (user_id is not None)
        if user_id is None:
            assert data["user"] is None
            assert data["permissions"]["role"] == "none"
        else:
            account = db.execute(
                "SELECT id, username, role FROM account WHERE id = %s",
                (user_id,),
            ).fetchone()
            assert data["user"] == {
                "id": account["id"],
                "username": account["username"],
            }
            assert data["permissions"]["role"] == account["role"]

    property_test()


def test_language_discovery_is_an_ordered_projection_of_reference_data(client, db):
    names = st.text(
        alphabet=st.characters(categories=("L", "N", "Zs")), min_size=1, max_size=30
    ).filter(lambda value: bool(value.strip()))

    @given(language_id=st.integers(min_value=1_000, max_value=10_000), name=names)
    def property_test(language_id, name):
        db.execute("DELETE FROM language WHERE id = %s", (language_id,))
        db.execute(
            "INSERT INTO language (id, name) VALUES (%s, %s)",
            (language_id, f"Generated {name.strip()}"),
        )
        db.commit()
        try:
            expected = [
                dict(row)
                for row in db.execute(
                    """SELECT id, name, tag, extlang, region, subvariant,
                              suppress_script, code3
                       FROM language ORDER BY name"""
                ).fetchall()
            ]
            assert client.get("/api/language").get_json()["result"] == expected
        finally:
            db.execute("DELETE FROM language WHERE id = %s", (language_id,))
            db.commit()

    property_test()


def test_submission_context_partitions_years_by_behavior(client, db):
    @given(open_2024=st.booleans(), open_2025=st.booleans())
    def property_test(open_2024, open_2025):
        db.execute(
            """UPDATE year SET submissions_open = CASE id
                   WHEN 2024 THEN %s WHEN 2025 THEN %s ELSE submissions_open END
               WHERE id IN (2024, 2025)""",
            (open_2024, open_2025),
        )
        db.commit()
        rows = db.execute(
            """SELECT id, submissions_open, special_name, special_short_name
               FROM year ORDER BY id"""
        ).fetchall()
        expected_open = [row["id"] for row in rows if row["id"] >= 0 and row["submissions_open"]]
        expected_closed = [
            row["id"] for row in rows if row["id"] >= 0 and not row["submissions_open"]
        ]
        expected_special_ids = [row["id"] for row in rows if row["id"] < 0]

        data = client.get("/api/submission-context").get_json()["result"]
        assert data["years"]["open"] == expected_open
        assert data["years"]["closed"] == expected_closed
        assert [year["id"] for year in data["years"]["specials"]] == expected_special_ids
        assert data["open_song_count"] == 0

    try:
        property_test()
    finally:
        db.execute(
            """UPDATE year SET submissions_open = CASE id
                   WHEN 2024 THEN false WHEN 2025 THEN true ELSE submissions_open END
               WHERE id IN (2024, 2025)"""
        )
        db.commit()
