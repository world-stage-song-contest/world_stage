"""Properties for the member submission-data endpoint."""

from hypothesis import given
from hypothesis import strategies as st


def test_reaching_either_submission_limit_forces_placeholder_mode(
    client, db, bob_headers, monkeypatch, login
):
    for country in ("US", "ES"):
        response = client.post(
            "/api/song",
            json={
                "year": 2025,
                "country": country,
                "title": f"{country} limit entry",
                "artist": "Limit Artist",
                "sources": "https://example.test/source",
                "languages": [20],
            },
            headers=bob_headers,
        )
        assert response.status_code == 201

    @given(limit=st.sampled_from(["user", "year"]))
    def property_test(limit):
        monkeypatch.setattr(
            "world_stage.routes.member.MAX_YEAR_SUBMISSIONS",
            2 if limit == "year" else 100,
        )
        login(2 if limit == "user" else 3)

        response = client.get("/member/submit/2025")

        assert response.status_code == 200
        countries = response.get_json()["countries"]
        assert countries["force_placeholder"] is True
        assert countries["force_placeholder_reason"]
        placeholders = {country["cc"] for country in countries["placeholder"]}
        assert "FR" in placeholders
        if limit == "user":
            assert {"US", "ES"} <= placeholders
        else:
            assert {"US", "ES"}.isdisjoint(placeholders)

    property_test()


def test_only_admins_can_edit_an_unowned_entry_from_a_closed_year(client, db, login):
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO country (id, name, is_participating, cc3)
               VALUES ('HU', 'Hungary', true, 'HUN')"""
        )
        cursor.execute(
            """INSERT INTO year (id, status, host_id)
               VALUES (1970, 'closed', 'HU')"""
        )
        cursor.execute(
            """WITH inserted AS (
                   INSERT INTO song (country_id, year_id)
                   VALUES ('HU', 1970) RETURNING id
               )
               INSERT INTO song_data (song_id, title, artist_credit_set_id)
               SELECT id, 'Unowned entry', test_artist_credit('Unknown') FROM inserted"""
        )
    db.commit()

    @given(user_id=st.sampled_from([1, 2, 3]))
    def property_test(user_id):
        login(user_id)

        response = client.get("/member/submit/1970")

        assert response.status_code == 200
        editable_countries = {
            country["cc"] for country in response.get_json()["countries"]["placeholder"]
        }
        assert ("HU" in editable_countries) is (user_id == 1)

    property_test()
