from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.song_revisions import create_song_revision


def _revise_song(db, song_id, **changes):
    db.rollback()
    with db.cursor() as cursor:
        create_song_revision(cursor, song_id, changes, changed_by=None)
    db.commit()


def _seed_show(db):
    with db.cursor() as cursor:
        show_number = cursor.execute(
            """SELECT COALESCE(MAX(show_number), 0) + 1 AS number
               FROM show WHERE year_id = 2025 AND show_type = 'sf'"""
        ).fetchone()["number"]
        show_id = cursor.execute(
            """INSERT INTO show (year_id, show_type, show_number)
               VALUES (2025, 'sf', %s) RETURNING id""",
            (show_number,),
        ).fetchone()["id"]
        song_id = cursor.execute(
            """INSERT INTO song (country_id, year_id)
               VALUES ('US', 2025) RETURNING id"""
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, artist_credit_set_id, title
               ) VALUES (
                   %s, 1, test_artist_credit('API Artist'), 'API Song'
               )""",
            (song_id,),
        )
        cursor.execute(
            """INSERT INTO song_show (song_id, show_id, running_order)
               VALUES (%s, %s, 1)""",
            (song_id, show_id),
        )
    db.commit()
    return song_id, f"2025-sf{show_number}"


def _recap(client, recap_type, selection, **query):
    return client.get(
        "/api/recap",
        query_string={"type": recap_type, "show": selection, **query},
    )


def _expected_snippets(first_start, first_end, second_start, second_end):
    if first_start is None and first_end is None:
        first_start, first_end = 50, 70
    if second_start is None and second_end is None:
        second_start = first_start
        second_end = second_start + 10 if second_start is not None else None
    elif second_start is not None and second_end is None:
        second_end = second_start + 10
    return {
        key: value
        for key, value in {
            "snippet_start": first_start,
            "snippet_end": first_end,
            "snippet2_start": second_start,
            "snippet2_end": second_end,
        }.items()
        if value is not None
    }


def test_recap_snippet_export_follows_configured_values_and_fallbacks(client, db):
    song_id, show = _seed_show(db)
    optional_time = st.one_of(st.none(), st.integers(min_value=0, max_value=600))

    @st.composite
    def second_snippet(draw):
        start = draw(optional_time)
        end = draw(
            optional_time
            if start is None
            else st.one_of(st.none(), st.integers(min_value=0, max_value=start + 10))
        )
        return start, end

    @settings(max_examples=20, deadline=None)
    @given(
        first_start=optional_time,
        first_end=optional_time,
        second=second_snippet(),
    )
    def property_test(first_start, first_end, second):
        second_start, second_end = second
        _revise_song(
            db,
            song_id,
            snippet_start=first_start,
            snippet_end=first_end,
            snippet2_start=second_start,
            snippet2_end=second_end,
        )
        response = _recap(client, "show", show)

        assert response.status_code == 200
        rows = response.get_json()["result"]
        assert len(rows) == 1
        exported = {key: value for key, value in rows[0].items() if key.startswith("snippet")}
        assert exported == _expected_snippets(first_start, first_end, second_start, second_end)
        assert rows[0]["title"] == "API Song"
        assert rows[0]["artist"] == "API Artist"

    property_test()


def test_country_selection_accepts_equivalent_identifiers(client, db):
    with db.cursor() as cursor:
        cursor.execute(
            """WITH inserted AS (
                   INSERT INTO song (country_id, year_id)
                   VALUES ('US', 2024) RETURNING id
               )
               INSERT INTO song_data (
                   song_id, submitter_id, artist_credit_set_id, title
               )
               SELECT id, 1, test_artist_credit('Country Artist'), 'Country Song'
               FROM inserted"""
        )
    db.commit()

    @given(selection=st.sampled_from(["US", "us", "USA", "usa", "United States", "united states"]))
    def property_test(selection):
        response = _recap(client, "country", selection)
        assert response.status_code == 200
        assert {row["title"] for row in response.get_json()["result"]} == {"Country Song"}

    property_test()


def test_special_year_filter_partitions_country_entries(client, db):
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO year (
                   id, status, host_id, special_name, special_short_name
               ) VALUES (-1, 'closed', 'US', 'Special Recap', 'sr')"""
        )
        for year, title in ((-1, "Special Song"), (2024, "Regular Song")):
            cursor.execute(
                """WITH inserted AS (
                       INSERT INTO song (country_id, year_id)
                       VALUES ('US', %s) RETURNING id
                   )
                   INSERT INTO song_data (
                       song_id, submitter_id, artist_credit_set_id, title
                   )
                   SELECT id, 1, test_artist_credit('Artist'), %s FROM inserted""",
                (year, title),
            )
    db.commit()

    @given(mode=st.sampled_from(["false", "true", "only"]))
    def property_test(mode):
        response = _recap(client, "country", "US", specials=mode)
        assert response.status_code == 200
        titles = {row["title"] for row in response.get_json()["result"]}
        expected = (
            {
                "Special Song",
                "Regular Song",
            }
            if mode == "true"
            else {"Special Song" if mode == "only" else "Regular Song"}
        )
        assert titles == expected

    property_test()


def test_recap_cache_validators_track_only_exported_behavior(client, db):
    song_id, show = _seed_show(db)

    @given(validator=st.sampled_from(["none-match", "match", "stale-match"]))
    def validator_property(validator):
        initial = _recap(client, "show", show)
        etag = initial.headers["ETag"]
        headers = {
            "none-match": {"If-None-Match": etag},
            "match": {"If-Match": etag},
            "stale-match": {"If-Match": '"stale"'},
        }[validator]
        response = client.get(
            "/api/recap",
            query_string={"type": "show", "show": show},
            headers=headers,
        )
        assert (
            response.status_code
            == {
                "none-match": 304,
                "match": 200,
                "stale-match": 412,
            }[validator]
        )

    validator_property()

    @given(exported_change=st.booleans())
    def change_property(exported_change):
        before = _recap(client, "show", show)
        current_title = before.get_json()["result"][0]["title"]
        changes = (
            {"title": current_title + " changed"}
            if exported_change
            else {"notes": "Unexported metadata"}
        )
        _revise_song(db, song_id, **changes)
        after = _recap(client, "show", show)
        assert (after.headers["ETag"] != before.headers["ETag"]) is exported_change

    change_property()


def test_recap_rejects_invalid_request_dimensions(client):
    @given(
        invalid=st.sampled_from(["type", "selection", "specials"]),
        value=st.text(max_size=12),
    )
    def property_test(invalid, value):
        query = {"type": "year", "show": "2024", "specials": "false"}
        if invalid == "type":
            query["type"] = value if value not in {"show", "year", "country", "submitter"} else ""
        elif invalid == "selection":
            query.pop("show")
        else:
            query["specials"] = value if value.lower() not in {"false", "true", "only"} else "bad"
        assert client.get("/api/recap", query_string=query).status_code == 400

    property_test()
