import datetime as dt

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.search import QueryError, decode_query, encode_query
from world_stage.search.query import temporal
from world_stage.search.service import execute_search


def field(name):
    return {"kind": "field", "name": name}


def literal(value):
    kind = (
        "null"
        if value is None
        else (
            "boolean"
            if type(value) is bool
            else "number"
            if isinstance(value, (int, float))
            else "string"
        )
    )
    return {"kind": kind, **({} if value is None else {"value": value})}


def comparison(name, operator, value, **modifiers):
    return {
        "kind": "comparison",
        "operator": operator,
        "left": field(name),
        "right": literal(value),
        "modifiers": modifiers,
    }


def combine(*nodes):
    return {"kind": "and", "operands": list(nodes)}


def negate(node):
    return {"kind": "not", "operand": node}


def query(node, order=None):
    return {"version": 1, "where": node, **({"orderBy": order} if order else {})}


def search(db, node, **kwargs):
    return execute_search(db, decode_query(query(node)), **kwargs)["results"]


def seed_entry(db, title, *, number=1, duration=None, native_title=None, lyrics=None):
    song_id = db.execute(
        "INSERT INTO song (country_id, year_id, entry_number) VALUES ('US', 2024, %s) RETURNING id",
        (number,),
    ).fetchone()["id"]
    db.execute(
        """INSERT INTO song_data (song_id, country_id, year_id, entry_number,
            title, native_title, duration, native_lyrics, artist_credit_set_id, submitter_id)
        VALUES (%s, 'US', 2024, %s, %s, %s, %s, %s, test_artist_credit('Search Artist'), 2)""",
        (song_id, number, title, native_title, duration, lyrics),
    )
    return str(song_id)


safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0"), max_size=40
)
comparisons = st.one_of(
    safe_text.map(lambda value: comparison("title", "equals", value)),
    st.integers(-10000, 10000).map(lambda value: comparison("year", "greater_than", value)),
    st.booleans().map(lambda value: comparison("special", "equals", value)),
    st.just(comparison("native_title", "equals", None)),
)
expressions = st.recursive(
    comparisons,
    lambda children: st.one_of(
        children.map(negate),
        st.lists(children, min_size=1, max_size=3).map(
            lambda nodes: {"kind": "and", "operands": nodes}
        ),
        st.lists(children, min_size=1, max_size=3).map(
            lambda nodes: {"kind": "or", "operands": nodes}
        ),
    ),
    max_leaves=12,
)


@given(node=expressions)
def test_json_round_trip_preserves_normalized_query(node):
    parsed = decode_query(query(node))
    assert decode_query(encode_query(parsed)) == parsed
    assert encode_query(decode_query(encode_query(parsed))) == encode_query(parsed)


@given(
    value=st.recursive(
        st.one_of(st.none(), st.booleans(), st.integers(), safe_text),
        lambda children: st.one_of(
            st.lists(children, max_size=4), st.dictionaries(safe_text, children, max_size=4)
        ),
        max_leaves=15,
    )
)
def test_arbitrary_json_is_rejected_cleanly(value):
    for document in (
        value,
        query(value),
        query(comparison("title", "equals", "x")) | {"extra": value},
    ):
        try:
            decoded = decode_query(document)
        except QueryError:
            continue
        assert decode_query(encode_query(decoded)) == decoded


@given(
    value=st.datetimes(min_value=dt.datetime(1900, 1, 1), max_value=dt.datetime(2100, 1, 1)),
    offset=st.integers(-12, 14),
)
def test_temporal_normalization_preserves_value(value, offset):
    value = value.replace(microsecond=0, tzinfo=dt.timezone(dt.timedelta(hours=offset)))
    encoded = temporal(value.isoformat(), "datetime", "")
    assert dt.datetime.fromisoformat(encoded) == value
    assert temporal(encoded, "datetime", "") == encoded


def test_numeric_filters_null_policies_and_negation(db, app, isolated_example):
    @settings(max_examples=20, deadline=None)
    @given(
        values=st.lists(st.one_of(st.none(), st.integers(0, 500)), min_size=1, max_size=8),
        bound=st.integers(0, 500),
        as_empty=st.booleans(),
        negative=st.booleans(),
    )
    def check(values, bound, as_empty, negative):
        with isolated_example(), app.test_request_context():
            ids = [
                seed_entry(db, f"Entry {i}", number=i + 1, duration=value)
                for i, value in enumerate(values)
            ]
            condition = comparison(
                "duration", "less_than", bound, nulls="as_empty" if as_empty else "distinct"
            )
            if negative:
                condition = negate(condition)
            actual = {r["id"] for r in search(db, condition)}
            expected = set()
            for song_id, value in zip(ids, values, strict=True):
                effective = 0 if value is None and as_empty else value
                if effective is not None and ((effective < bound) != negative):
                    expected.add(song_id)
            assert actual == expected

    check()


def test_text_literals_patterns_and_missingness(db, app, isolated_example):
    @settings(max_examples=25, deadline=None)
    @given(value=safe_text, as_empty=st.booleans(), negative=st.booleans())
    def check(value, as_empty, negative):
        with isolated_example(), app.test_request_context():
            song_id = seed_entry(db, value)
            seed_entry(db, value + " extra", number=2, native_title="Present")
            exact = comparison("title", "equals", value, case="sensitive", accent="sensitive")
            assert [r["id"] for r in search(db, exact)] == [song_id]
            pattern = "".join("\\" + char if char in "*?\\" else char for char in value)
            matching = comparison("title", "like", pattern, case="sensitive", accent="sensitive")
            assert [r["id"] for r in search(db, matching)] == [song_id]
            condition = comparison(
                "native_title", "equals", "", nulls=("as_empty" if as_empty else "distinct")
            )
            if negative:
                condition = negate(condition)
            selected = combine(comparison("id", "equals", song_id), condition)
            assert bool(search(db, selected)) == (as_empty and not negative)
            assert [r["id"] for r in search(db, comparison("native_title", "equals", None))] == [
                song_id
            ]

    check()


def test_normalization_does_not_turn_literal_characters_into_wildcards(db, app, isolated_example):
    @settings(max_examples=12, deadline=None)
    @given(
        character=st.sampled_from(["%", "_", "\\", "％", "＿", "＼"]),
        suffix=st.text(alphabet="abc", min_size=1, max_size=8),
    )
    def check(character, suffix):
        with isolated_example(), app.test_request_context():
            song_id = seed_entry(db, character + suffix)
            seed_entry(db, "other" + suffix, number=2)
            for operator in ("equals", "contains", "starts_with"):
                assert [
                    r["id"] for r in search(db, comparison("title", operator, character + suffix))
                ] == [song_id]
            pattern = "".join("\\" + c if c in "*?\\" else c for c in character + suffix)
            assert [r["id"] for r in search(db, comparison("title", "like", pattern))] == [song_id]

    check()


def test_search_uses_current_revisions_and_includes_lyrics(db, app, isolated_example):
    @settings(max_examples=12, deadline=None)
    @given(suffix=st.text(alphabet="abcdef", min_size=1, max_size=12), placeholder=st.booleans())
    def check(suffix, placeholder):
        with isolated_example(), app.test_request_context():
            song_id = seed_entry(db, "Old " + suffix, lyrics="sing\ntogether " + suffix)
            revision = db.execute(
                """INSERT INTO song_data (song_id, country_id, year_id, entry_number,
                   title, native_lyrics, artist_credit_set_id, submitter_id)
                   SELECT song_id, country_id, year_id, entry_number, %s,
                          native_lyrics, artist_credit_set_id, submitter_id
                   FROM song_data WHERE song_id = %s RETURNING id""",
                ("New " + suffix, int(song_id)),
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO song_status (song_id, song_data_id, is_placeholder) "
                "VALUES (%s, %s, %s)",
                (int(song_id), revision, placeholder),
            )
            assert search(db, comparison("title", "equals", "Old " + suffix)) == []
            assert bool(search(db, comparison("title", "equals", "New " + suffix))) != placeholder
            assert (
                bool(
                    search(
                        db,
                        combine(
                            comparison("type", "equals", "entry"),
                            comparison("text", "phrase", "sing together " + suffix),
                        ),
                    )
                )
                != placeholder
            )

    check()


def test_all_page_categories_and_pagination(db, app, isolated_example):
    @settings(max_examples=8, deadline=None)
    @given(page_size=st.integers(1, 6))
    def check(page_size):
        with isolated_example(), app.test_request_context():
            seed_entry(db, "Song")
            db.execute("INSERT INTO show_status (name) VALUES ('draw') ON CONFLICT DO NOTHING")
            show_id = db.execute("""INSERT INTO show (year_id, show_type, status)
                       VALUES (2024, 'f', 'draw') RETURNING id""").fetchone()["id"]
            db.execute(
                "INSERT INTO song_show (song_id, show_id) SELECT id, %s FROM song", (show_id,)
            )
            node = comparison("name", "contains", "")
            all_results = search(db, node, limit=100)
            assert {r["type"] for r in all_results} == {
                "entry",
                "year",
                "country",
                "submitter",
                "artist",
                "show",
            }
            pages = []
            for offset in range(0, len(all_results), page_size):
                pages.extend(search(db, node, limit=page_size, offset=offset))
            assert pages == all_results
            assert len({(r["type"], r["id"]) for r in pages}) == len(pages)
            assert all(r["url"].startswith("/") for r in pages)

    check()


def test_api_validates_and_executes_the_same_json(client):
    @settings(max_examples=12, deadline=None)
    @given(year=st.integers(2000, 2050), invalid=st.booleans())
    def check(year, invalid):
        node = comparison("year", "equals", str(year) if invalid else year)
        response = client.post("/api/search", json={"query": query(node)})
        assert response.status_code == (400 if invalid else 200)
        payload = response.get_json()
        if invalid:
            assert payload["error"]["path"].startswith("/where")
        else:
            assert all(r["data"]["year"] == year for r in payload["result"]["results"])
            assert payload["result"]["query"] == encode_query(decode_query(query(node)))

    check()


def test_genres_are_display_labels_and_negation_excludes_any_match(db, app, isolated_example):
    @settings(max_examples=12, deadline=None)
    @given(
        labels=st.lists(
            st.text(alphabet="abcdef", min_size=1, max_size=12), min_size=1, max_size=4, unique=True
        ),
        nulls=st.booleans(),
    )
    def check(labels, nulls):
        with isolated_example(), app.test_request_context():
            song_id = seed_entry(db, "With genres")
            empty_id = seed_entry(db, "Without genres", number=2)
            genre = db.execute(
                "INSERT INTO genre (name) VALUES ('Internal grouping') RETURNING id"
            ).fetchone()["id"]
            ids = [
                db.execute(
                    "INSERT INTO subgenre (genre_id, name) VALUES (%s, %s) RETURNING id",
                    (genre, label),
                ).fetchone()["id"]
                for label in labels
            ]
            genre_set = db.execute(
                "INSERT INTO genre_set (subgenre_ids) VALUES (%s) RETURNING id", (ids,)
            ).fetchone()["id"]
            for priority, subgenre in enumerate(ids, 1):
                db.execute(
                    "INSERT INTO genre_set_subgenre (genre_set_id, subgenre_id, priority) "
                    "VALUES (%s, %s, %s)",
                    (genre_set, subgenre, priority),
                )
            db.execute(
                "UPDATE song_data SET genre_set_id = %s WHERE song_id = %s",
                (genre_set, int(song_id)),
            )
            assert search(db, comparison("genre", "equals", "Internal grouping")) == []
            for label in labels:
                condition = comparison(
                    "genre", "equals", label, nulls="as_empty" if nulls else "distinct"
                )
                assert [r["id"] for r in search(db, condition)] == [song_id]
                assert {r["id"] for r in search(db, negate(condition))} == (
                    {empty_id} if nulls else set()
                )
            assert [r["id"] for r in search(db, comparison("genre", "equals", None))] == [empty_id]

    check()


def test_fuzzy_search_respects_sensitivity_and_prefers_exact_matches(db, app, isolated_example):
    @settings(max_examples=12, deadline=None)
    @given(word=st.text(alphabet="abcdef", min_size=6, max_size=12))
    def check(word):
        with isolated_example(), app.test_request_context():
            exact_id = seed_entry(db, word)
            approximate_id = seed_entry(db, word[:-1] + "x", number=2)
            uppercase_id = seed_entry(db, word.upper(), number=3)
            insensitive = search(db, comparison("title", "fuzzy", word))
            assert {r["id"] for r in insensitive} == {exact_id, approximate_id, uppercase_id}
            assert insensitive[0]["id"] in (exact_id, uppercase_id)
            sensitive = search(db, comparison("title", "fuzzy", word, case="sensitive"))
            assert {r["id"] for r in sensitive} == {exact_id, approximate_id}
            assert sensitive[0]["id"] == exact_id

    check()


def test_temporal_fields_and_sentinels_use_one_query_clock(db, app, isolated_example):
    @settings(max_examples=12, deadline=None)
    @given(
        day=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2050, 1, 1)),
        day_offset=st.integers(-2, 2),
    )
    def check(day, day_offset):
        with isolated_example(), app.test_request_context():
            song_id = seed_entry(db, "Dated show")
            db.execute("INSERT INTO show_status (name) VALUES ('draw') ON CONFLICT DO NOTHING")
            show = db.execute(
                """INSERT INTO show (year_id, show_type, status, date, voting_opens)
                VALUES (2024, 'f', 'draw', %s, %s) RETURNING id""",
                (
                    dt.datetime.combine(day + dt.timedelta(days=day_offset), dt.time(12), dt.UTC),
                    dt.datetime.combine(day, dt.time(12), dt.UTC),
                ),
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO song_show (song_id, show_id) VALUES (%s, %s)", (int(song_id), show)
            )
            now = dt.datetime.combine(day, dt.time(12), dt.UTC)
            condition = comparison("date", "equals", None)
            condition["right"] = {"kind": "temporal_sentinel", "name": "today"}
            assert bool(search(db, condition, now=now, timezone="UTC")) == (day_offset == 0)
            condition = comparison("voting_opens", "equals", None)
            condition["right"] = {"kind": "temporal_sentinel", "name": "now"}
            assert [r["id"] for r in search(db, condition, now=now)] == [str(show)]
            condition["right"] = {"kind": "datetime", "value": day.isoformat() + "T13:00:00+01:00"}
            assert [r["id"] for r in search(db, condition)] == [str(show)]

    check()
