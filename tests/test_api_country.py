import string

from hypothesis import given
from hypothesis import strategies as st


def test_country_index_matches_participation_filter(client, db):
    @given(include_all=st.booleans())
    def property_test(include_all):
        expected = {
            row["id"]
            for row in db.execute(
                """SELECT id FROM country
                   WHERE id <> 'XX' AND (%s OR is_participating)""",
                (include_all,),
            ).fetchall()
        }

        response = client.get("/api/country", query_string={"all": str(include_all).lower()})

        assert response.status_code == 200
        assert {country["id"] for country in response.get_json()["result"]} == expected

    property_test()


def test_country_codes_are_case_insensitive_and_cc3_is_canonicalized(client, db):
    countries = db.execute(
        "SELECT id, cc3 FROM country WHERE id <> 'XX' AND cc3 IS NOT NULL"
    ).fetchall()

    @given(
        country=st.sampled_from(countries),
        lowercase=st.booleans(),
        use_cc3=st.booleans(),
    )
    def property_test(country, lowercase, use_cc3):
        code = country["cc3"] if use_cc3 else country["id"]
        code = code.lower() if lowercase else code.upper()

        response = client.get(f"/api/country/{code}")

        if use_cc3:
            assert response.status_code == 301
            response = client.get(response.headers["Location"])
        assert response.status_code == 200
        assert response.get_json()["result"]["id"] == country["id"]

    property_test()


def test_unknown_country_codes_are_rejected(client, db):
    known_codes = {
        code
        for row in db.execute("SELECT id, cc3 FROM country").fetchall()
        for code in (row["id"], row["cc3"])
        if code
    }

    @given(
        code=st.text(
            alphabet=string.ascii_uppercase,
            min_size=2,
            max_size=3,
        ).filter(lambda code: code not in known_codes)
    )
    def property_test(code):
        response = client.get(f"/api/country/{code}")

        assert response.status_code == 404

    property_test()
