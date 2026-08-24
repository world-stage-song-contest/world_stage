import string

from hypothesis import given, settings
from hypothesis import strategies as st


def _create_song(client, headers, **overrides):
    return client.post(
        "/api/song",
        headers=headers,
        json={
            "year": 2025,
            "country": "US",
            "title": "Test Song",
            "artist": "Test Artist",
            "sources": "https://example.test/source",
            "languages": [20],
            **overrides,
        },
    )


def _put_song(client, headers, song_id, **overrides):
    return client.put(
        f"/api/song/{song_id}",
        headers=headers,
        json={
            "year": 2025,
            "country": "US",
            "title": "Replacement Title",
            "artist": "Replacement Artist",
            "sources": "https://example.test/replacement",
            "languages": [20],
            **overrides,
        },
    )


def _result(response):
    return response.get_json()["result"]


def _delete_if_current(client, headers, song_id):
    client.delete(f"/api/song/{song_id}", headers=headers)


SAFE_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " -_.,!?",
    min_size=1,
    max_size=35,
).filter(lambda value: bool(value.strip()))


def test_song_crud_round_trip_preserves_generated_catalog_values(
    client, bob_headers, alice_headers
):
    language_order = st.lists(st.sampled_from([20, 30, 40]), min_size=1, max_size=3, unique=True)

    @settings(max_examples=12, deadline=None)
    @given(
        country=st.sampled_from(["US", "ES", "FR"]),
        title=SAFE_TEXT,
        artist=SAFE_TEXT,
        languages=language_order,
        request_encoding=st.sampled_from(["json", "form"]),
    )
    def property_test(country, title, artist, languages, request_encoding):
        if request_encoding == "json":
            response = _create_song(
                client,
                bob_headers,
                country=country,
                title=title,
                artist=artist,
                languages=languages,
            )
        else:
            response = client.post(
                "/api/song",
                data={
                    "year": "2025",
                    "country": country,
                    "title": title,
                    "artist": artist,
                    "sources": "https://example.test/source",
                    "language": [str(language) for language in languages],
                },
                headers={
                    "Authorization": "Bearer token-bob",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        assert response.status_code == 201
        created = _result(response)
        song_id = created["id"]
        try:
            loaded = _result(client.get(f"/api/song/{song_id}"))
            assert {
                "id": loaded["id"],
                "country_id": loaded["country_id"],
                "year": loaded["year"],
                "title": loaded["title"],
                "artist": loaded["artist"],
            } == {
                "id": song_id,
                "country_id": country,
                "year": 2025,
                "title": title.strip(),
                "artist": artist.strip(),
            }
            assert [language["id"] for language in loaded["languages"]] == languages
            by_slot = client.get(f"/api/song/{country.lower()}/2025")
            assert by_slot.status_code == 200
            assert _result(by_slot)["id"] == song_id

        finally:
            _delete_if_current(client, alice_headers, song_id)

    property_test()


def test_entry_code_is_admin_only_and_limited_to_ten_characters(
    client, bob_headers, alice_headers
):
    created = _create_song(client, bob_headers, country="FR")
    assert created.status_code == 201
    song_id = _result(created)["id"]

    @given(
        method=st.sampled_from(["patch", "put"]),
        is_admin=st.booleans(),
        code=st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=12),
    )
    def property_test(method, is_admin, code):
        headers = alice_headers if is_admin else bob_headers
        response = (
            client.patch(
                f"/api/song/{song_id}", json={"entry_code": code}, headers=headers
            )
            if method == "patch"
            else _put_song(client, headers, song_id, entry_code=code)
        )
        expected = 403 if not is_admin else (200 if len(code) <= 10 else 400)
        assert response.status_code == expected
        if expected == 200:
            assert _result(response)["entry_code"] == code

    try:
        property_test()
    finally:
        _delete_if_current(client, alice_headers, song_id)


def test_country_and_artist_lookup_accept_equivalent_public_identifiers(
    client, bob_headers, alice_headers
):
    created = _result(
        _create_song(
            client,
            bob_headers,
            country="ES",
            artist=None,
            artists=[
                {
                    "full_name": "Aleksandra Nowak",
                    "native_name": "Александра Новак",
                    "stage_name": "Alexa",
                }
            ],
        )
    )
    song_id = created["id"]
    artist_id = created["artists"][0]["id"]
    try:

        @given(query=st.sampled_from(["Aleksandra", "Александра", "Alexa"]))
        def artist_property(query):
            matches = _result(client.get("/api/song/artists", query_string={"q": query}))
            assert any(artist["id"] == artist_id for artist in matches)

        artist_property()

        @given(code=st.sampled_from(["ES", "es", "ESP", "esp"]))
        def country_property(code):
            response = client.get(f"/api/song/{code}/2025", follow_redirects=True)
            assert response.status_code == 200
            assert _result(response)["id"] == song_id

        country_property()
    finally:
        _delete_if_current(client, alice_headers, song_id)


def test_structured_artist_credits_preserve_order_and_joins(client, alice_headers):
    joins = st.lists(
        st.sampled_from([" feat. ", " & ", " x ", " ~ duet with ~ "]),
        min_size=1,
        max_size=2,
    )

    @settings(max_examples=8, deadline=None)
    @given(generated_joins=joins, use_stage_names=st.booleans())
    def property_test(generated_joins, use_stage_names):
        count = len(generated_joins) + 1
        artists = []
        for index in range(count):
            full_name = f"Canonical Artist {index}"
            artists.append(
                {
                    "full_name": full_name,
                    "stage_name": f"Stage {index}" if use_stage_names else None,
                    "join": None if index == 0 else generated_joins[index - 1],
                }
            )
        response = _create_song(
            client,
            alice_headers,
            country="US",
            artist=None,
            artists=artists,
        )
        assert response.status_code == 201
        song = _result(response)
        try:
            rendered_names = [artist["stage_name"] or artist["full_name"] for artist in artists]
            expected = rendered_names[0] + "".join(
                join + name for join, name in zip(generated_joins, rendered_names[1:], strict=True)
            )
            assert song["artist"] == expected
            assert [credit["join"] for credit in song["artists"]] == [
                None,
                *generated_joins,
            ]
        finally:
            _delete_if_current(client, alice_headers, song["id"])

    property_test()


def test_creation_validation_rejects_missing_or_unknown_required_dimensions(client, bob_headers):
    @given(
        invalid=st.sampled_from(
            [
                "auth",
                "year",
                "country",
                "languages",
                "title",
                "artist",
                "sources",
                "unknown-year",
                "unknown-country",
            ]
        )
    )
    def property_test(invalid):
        body = {
            "year": 2025,
            "country": "US",
            "title": "Valid title",
            "artist": "Valid artist",
            "sources": "https://example.test/source",
            "languages": [20],
        }
        headers = bob_headers
        if invalid == "auth":
            headers = None
        elif invalid in {"year", "country"}:
            body.pop(invalid)
        elif invalid in {"languages", "title", "artist", "sources"}:
            body[invalid] = [] if invalid == "languages" else ""
        elif invalid == "unknown-year":
            body["year"] = 1800
        else:
            body["country"] = "ZZ"
        response = client.post("/api/song", json=body, headers=headers)
        assert response.status_code == (
            401 if invalid == "auth" else 404 if invalid.startswith("unknown") else 400
        )

    property_test()


def test_submission_gate_depends_on_submission_availability_not_year_label(
    client, db, bob_headers, alice_headers
):
    @given(
        submissions_open=st.booleans(),
        status=st.sampled_from(["open", "ongoing", "closed"]),
    )
    def property_test(submissions_open, status):
        db.execute(
            "UPDATE year SET submissions_open = %s, status = %s WHERE id = 2025",
            (submissions_open, status),
        )
        db.commit()
        response = _create_song(client, bob_headers)
        assert response.status_code == (201 if submissions_open else 403)
        if response.status_code == 201:
            _delete_if_current(client, alice_headers, _result(response)["id"])

    try:
        property_test()
    finally:
        db.execute("UPDATE year SET submissions_open = true, status = 'open' WHERE id = 2025")
        db.commit()


def test_user_submission_limit_exempts_placeholders_and_admins(client, bob_headers, alice_headers):
    occupied = [
        _result(_create_song(client, bob_headers, country=country))["id"]
        for country in ("US", "ES")
    ]

    @given(actor=st.sampled_from(["user", "admin"]), placeholder=st.booleans())
    def property_test(actor, placeholder):
        headers = bob_headers if actor == "user" else alice_headers
        response = _create_song(
            client,
            headers,
            country="FR",
            is_placeholder=placeholder,
        )
        accepted = actor == "admin" or placeholder
        assert response.status_code == (201 if accepted else 403)
        if accepted:
            assert _result(response)["is_placeholder"] is placeholder
            _delete_if_current(client, alice_headers, _result(response)["id"])

    try:
        property_test()
    finally:
        for song_id in occupied:
            _delete_if_current(client, alice_headers, song_id)


def test_write_permissions_follow_owner_and_admin_roles(
    client, db, bob_headers, carol_headers, alice_headers
):
    @settings(max_examples=24, deadline=None)
    @given(
        operation_and_state=st.one_of(
            st.sampled_from([("patch", False), ("put", False)]),
            st.tuples(st.just("delete"), st.booleans()),
        ),
        actor=st.sampled_from(["anonymous", "owner", "other", "admin"]),
        nonexistent=st.booleans(),
        title=SAFE_TEXT,
    )
    def property_test(operation_and_state, actor, nonexistent, title):
        operation, closed_year = operation_and_state
        if nonexistent:
            song_id = 9_999_999
        elif closed_year:
            with db.cursor() as cursor:
                song_id = cursor.execute(
                    """INSERT INTO song (country_id, year_id, entry_number)
                       SELECT 'US', 2024, COALESCE(MAX(entry_number), 0) + 1
                       FROM song WHERE country_id = 'US' AND year_id = 2024
                       RETURNING id"""
                ).fetchone()["id"]
                cursor.execute(
                    """INSERT INTO song_data (
                           song_id, submitter_id, title, artist_credit_set_id
                       ) VALUES (
                           %s, 2, 'Closed Song', test_artist_credit('Artist')
                       )""",
                    (song_id,),
                )
            db.commit()
        else:
            song_id = _result(_create_song(client, bob_headers))["id"]
        headers = {
            "anonymous": None,
            "owner": bob_headers,
            "other": carol_headers,
            "admin": alice_headers,
        }[actor]
        if operation == "patch":
            response = client.patch(f"/api/song/{song_id}", json={"title": title}, headers=headers)
        elif operation == "put":
            response = (
                _put_song(client, headers or {}, song_id, title=title)
                if headers
                else client.put(
                    f"/api/song/{song_id}",
                    json={
                        "year": 2025,
                        "country": "US",
                        "title": title,
                        "artist": "Artist",
                        "sources": "source",
                        "languages": [20],
                    },
                )
            )
        else:
            response = client.delete(f"/api/song/{song_id}", headers=headers)
        if nonexistent:
            expected = 401 if actor == "anonymous" else 204 if operation == "delete" else 404
        elif actor == "anonymous":
            expected = 401
        elif actor == "other" or (closed_year and actor != "admin"):
            expected = 403
        else:
            expected = 204 if operation == "delete" else 200
        assert response.status_code == expected
        if expected == 200:
            assert _result(response)["title"] == title.strip()
        if operation == "delete" and expected == 204 and not nonexistent:
            assert client.get(f"/api/song/{song_id}").status_code == 404
        else:
            _delete_if_current(client, alice_headers, song_id)

    property_test()


def test_patch_preserves_omitted_values_while_put_replaces_the_resource(
    client, bob_headers, alice_headers
):
    @settings(max_examples=10, deadline=None)
    @given(
        method=st.sampled_from(["patch", "put"]),
        title=SAFE_TEXT,
        languages=st.lists(st.sampled_from([20, 30, 40]), min_size=1, max_size=3, unique=True),
    )
    def property_test(method, title, languages):
        song_id = _result(
            _create_song(
                client,
                bob_headers,
                notes="Original notes",
                video_link="https://example.test/video",
                languages=[20],
            )
        )["id"]
        try:
            if method == "patch":
                response = client.patch(
                    f"/api/song/{song_id}",
                    headers=bob_headers,
                    json={"title": title, "languages": languages},
                )
            else:
                response = _put_song(
                    client,
                    bob_headers,
                    song_id,
                    title=title,
                    languages=languages,
                )
            assert response.status_code == 200
            updated = _result(response)
            assert updated["title"] == title.strip()
            assert [language["id"] for language in updated["languages"]] == languages
            assert updated["notes"] == ("Original notes" if method == "patch" else None)
            assert updated["video_link"] == (
                "https://example.test/video" if method == "patch" else None
            )
        finally:
            _delete_if_current(client, alice_headers, song_id)

    property_test()


def test_snippet_limits_apply_consistently_to_create_patch_and_replace(
    client, bob_headers, alice_headers
):
    @settings(max_examples=18, deadline=None)
    @given(
        method=st.sampled_from(["create", "patch", "put"]),
        snippet=st.sampled_from(["first", "second"]),
        start=st.integers(1, 300),
        duration=st.integers(0, 30),
    )
    def property_test(method, snippet, start, duration):
        prefix = "snippet" if snippet == "first" else "snippet2"
        values = {
            f"{prefix}_start": f"{start // 60}:{start % 60:02d}",
            f"{prefix}_end": f"{(start + duration) // 60}:{(start + duration) % 60:02d}",
        }
        song_id = None
        try:
            if method == "create":
                response = _create_song(client, bob_headers, **values)
            else:
                baseline = _create_song(client, bob_headers)
                assert baseline.status_code == 201
                song_id = _result(baseline)["id"]
                response = (
                    client.patch(f"/api/song/{song_id}", json=values, headers=bob_headers)
                    if method == "patch"
                    else _put_song(client, bob_headers, song_id, **values)
                )
            limit = 20 if snippet == "first" else 10
            expected = 201 if method == "create" else 200
            assert response.status_code == (expected if duration <= limit else 400)
            if response.status_code in {200, 201}:
                result = _result(response)
                song_id = result["id"]
                assert result[f"{prefix}_start"] == values[f"{prefix}_start"]
                assert result[f"{prefix}_end"] == values[f"{prefix}_end"]
        finally:
            if song_id is not None:
                _delete_if_current(client, alice_headers, song_id)

    property_test()


def test_approval_state_is_not_writable_through_song_crud_and_placeholder_is_status_only(
    client, db, bob_headers, alice_headers
):
    @given(method=st.sampled_from(["create", "patch", "put"]), placeholder=st.booleans())
    def property_test(method, placeholder):
        if method == "create":
            response = _create_song(
                client,
                alice_headers,
                approval_status="accepted",
                is_placeholder=placeholder,
            )
            song_id = _result(response)["id"]
        else:
            song_id = _result(_create_song(client, bob_headers))["id"]
            before_revisions = db.execute(
                "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s",
                (song_id,),
            ).fetchone()["count"]
            response = (
                client.patch(
                    f"/api/song/{song_id}",
                    headers=alice_headers,
                    json={
                        "approval_status": "accepted",
                        "is_placeholder": placeholder,
                    },
                )
                if method == "patch"
                else _put_song(
                    client,
                    alice_headers,
                    song_id,
                    approval_status="accepted",
                    is_placeholder=placeholder,
                )
            )
            if method == "patch" and response.status_code == 200:
                after_revisions = db.execute(
                    "SELECT COUNT(*) AS count FROM song_data WHERE song_id = %s",
                    (song_id,),
                ).fetchone()["count"]
                assert after_revisions == before_revisions
        assert response.status_code in {200, 201}
        assert "approval_status" not in _result(response)
        current = db.execute(
            """SELECT approval_status, is_placeholder
               FROM current_song WHERE id = %s""",
            (song_id,),
        ).fetchone()
        assert current == {
            "approval_status": "pending",
            "is_placeholder": placeholder,
        }
        _delete_if_current(client, alice_headers, song_id)

    property_test()


def test_media_duration_tracks_only_internal_media_link_changes(
    client, db, bob_headers, alice_headers, monkeypatch
):
    internal_one = "https://media.world-stage.org/one.mp4"
    internal_two = "https://media.world-stage.org/two.mp4"
    external = "https://example.test/video.mp4"

    @settings(max_examples=10, deadline=None)
    @given(
        initial_internal=st.booleans(),
        next_link=st.sampled_from([internal_one, internal_two, external, ""]),
        duration=st.floats(min_value=1, max_value=600, allow_nan=False, allow_infinity=False),
    )
    def property_test(initial_internal, next_link, duration):
        def probe(_url):
            return duration

        monkeypatch.setattr("world_stage.media.probe_duration", probe)
        initial_link = internal_one if initial_internal else external
        song_id = _result(_create_song(client, bob_headers, video_link=initial_link))["id"]
        try:
            response = client.patch(
                f"/api/song/{song_id}",
                json={"video_link": next_link},
                headers=bob_headers,
            )
            assert response.status_code == 200
            stored = db.execute(
                "SELECT video_link, duration FROM current_song WHERE id = %s",
                (song_id,),
            ).fetchone()
            normalized_next = next_link or None
            assert stored["video_link"] == normalized_next
            if next_link.startswith("https://media.world-stage.org/"):
                if next_link == initial_link:
                    expected_duration = duration if initial_internal else None
                else:
                    expected_duration = duration
            else:
                expected_duration = None
            assert stored["duration"] == expected_duration
        finally:
            _delete_if_current(client, alice_headers, song_id)

    property_test()
