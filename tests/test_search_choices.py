from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.search import parse_wsql
from world_stage.search.choices import search_choices


def test_person_suggestions_require_a_public_search_entry(db, isolated_example):
    @settings(max_examples=12, deadline=None)
    @given(name=st.text(alphabet="abcXYZé星", min_size=1, max_size=20))
    def check(name):
        with isolated_example():
            name = "Unlisted " + name
            credit_id = db.execute("SELECT test_artist_credit(%s) AS id", (name,)).fetchone()["id"]
            assert name not in {item["value"] for item in search_choices(db, "entry", "artist")}
            assert "bob" not in {item["value"] for item in search_choices(db, "entry", "submitter")}
            song_id = db.execute(
                "INSERT INTO song (country_id, year_id, entry_number) "
                "VALUES ('US', 2024, 1) RETURNING id"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO song_data (song_id, country_id, year_id, entry_number, "
                "title, artist_credit_set_id, submitter_id) "
                "VALUES (%s, 'US', 2024, 1, 'Public entry', %s, 2)",
                (song_id, credit_id),
            )
            assert name in {item["value"] for item in search_choices(db, "entry", "artist")}
            assert "bob" in {item["value"] for item in search_choices(db, "entry", "submitter")}

    check()


def test_unused_reference_values_are_available_with_round_trip_literals(db, isolated_example):
    @settings(max_examples=20, deadline=None)
    @given(
        names=st.lists(
            st.text(alphabet="abcXYZé星'\"\\\n", min_size=1, max_size=20),
            min_size=1,
            max_size=10,
            unique=True,
        )
    )
    def check(names):
        with isolated_example():
            genre_id = db.execute(
                "INSERT INTO genre (name) VALUES ('Choices') RETURNING id"
            ).fetchone()["id"]
            for name in names:
                db.execute(
                    "INSERT INTO subgenre (genre_id, name) VALUES (%s, %s)", (genre_id, name)
                )
                db.execute(
                    "INSERT INTO year_status (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,)
                )
            for category, field in [("entry", "genre"), ("year", "status")]:
                options = search_choices(db, category, field)
                assert set(names) <= {option["value"] for option in options}
                for option in options:
                    assert (
                        parse_wsql(f"{field} = {option['literal']}").where.right.value
                        == option["value"]
                    )

    check()


def test_choice_api_only_exposes_fields_for_the_selected_type(client):
    @settings(max_examples=20, deadline=None)
    @given(
        category=st.sampled_from(["entry", "year", "show", "artist", "country", "submitter"]),
        field=st.sampled_from(["genre", "status", "country", "year", "show_type", "title"]),
    )
    def check(category, field):
        allowed = {
            "genre": ("entry",),
            "status": ("year", "show"),
            "country": ("entry", "year", "country"),
            "year": ("entry", "year", "show"),
            "show_type": ("show",),
        }
        response = client.get(
            "/api/search/choices", query_string={"type": category, "field": field}
        )
        assert response.status_code == (200 if category in allowed.get(field, ()) else 400)
        if response.status_code == 200:
            for option in response.get_json()["result"]:
                assert (
                    parse_wsql(f"{field} = {option['literal']}").where.right.value
                    == option["value"]
                )

    check()
