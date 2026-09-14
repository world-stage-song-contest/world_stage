from hypothesis import given, settings
from hypothesis import strategies as st

MEDIA = "https://media.world-stage.org"


def _show(db):
    show_id = db.execute(
        "INSERT INTO show (year_id, show_type) VALUES (2025, 'f') RETURNING id"
    ).fetchone()["id"]
    db.commit()
    return show_id


def test_metadata_updates_round_trip_and_preserve_unspecified_values(client, db, alice_headers):
    _show(db)

    @settings(max_examples=20, deadline=None)
    @given(
        opening=st.booleans(), countdown=st.booleans(),
        intervals=st.lists(st.integers(0, 100), max_size=9),
        method=st.sampled_from(["POST", "PUT"]),
    )
    def check(opening, countdown, intervals, method):
        for path, initial, patch in (
            ("/api/year/2025/metadata", {"opening": opening}, {"countdown": countdown}),
            ("/api/show/2025-f/metadata", {"opening": f"{MEDIA}/opening.mov"},
             {"intervals": [f"{MEDIA}/act-{value}.mov" for value in intervals]}),
        ):
            response = client.open(path, method=method, json=initial, headers=alice_headers)
            assert response.status_code == 200
            assert response.get_json()["result"] == initial
            response = client.patch(path, json=patch, headers=alice_headers)
            assert response.status_code == 200
            assert response.get_json()["result"] == initial | patch
            assert client.get(path).get_json()["result"] == initial | patch
            assert client.open(
                path, method=method, json={}, headers=alice_headers
            ).get_json()["result"] == {}
            assert client.get(path).get_json()["result"] == {}

    try:
        check()
    finally:
        db.execute("UPDATE year SET metadata = '{}' WHERE id = 2025")
        db.commit()


def test_only_admins_can_write_metadata(client, db, alice_headers, bob_headers):
    _show(db)

    @settings(max_examples=24, deadline=None)
    @given(
        role=st.sampled_from(["user", "editor", "admin", "owner"]),
        method=st.sampled_from(["POST", "PUT", "PATCH"]),
        year=st.booleans(), anonymous=st.booleans(),
    )
    def check(role, method, year, anonymous):
        db.execute("UPDATE account SET role = %s WHERE id = 2", (role,))
        db.commit()
        path = "/api/year/2025/metadata" if year else "/api/show/2025-f/metadata"
        initial = {"opening": False} if year else {"opening": None}
        assert client.put(path, json=initial, headers=alice_headers).status_code == 200
        patch = {"opening": True} if year else {"opening": f"{MEDIA}/custom.mov"}
        response = client.open(path, method=method, json=patch,
                               headers={} if anonymous else bob_headers)
        expected = 401 if anonymous else 200 if role in ("admin", "owner") else 403
        assert response.status_code == expected
        assert client.get(path).get_json()["result"] == (patch if expected == 200 else initial)

    try:
        check()
    finally:
        db.execute("UPDATE year SET metadata = '{}' WHERE id = 2025")
        db.commit()


def test_invalid_metadata_is_rejected_without_changes(client, db, alice_headers):
    _show(db)

    @settings(max_examples=24, deadline=None)
    @given(
        method=st.sampled_from(["POST", "PUT", "PATCH"]),
        value=st.one_of(st.none(), st.integers(), st.lists(st.booleans()), st.text()),
    )
    def check(method, value):
        for path, body in (
            ("/api/year/2025/metadata", {"opening": value}),
            ("/api/show/2025-f/metadata", {"intervals": value}),
        ):
            if body.get("intervals") == []:
                body = {"opening": "https://other.example/opening.mov"}
            before = client.get(path).get_json()
            response = client.open(path, method=method, json=body, headers=alice_headers)
            assert response.status_code == 400
            assert client.get(path).get_json() == before

    check()


def test_missing_metadata_targets_return_not_found(client, alice_headers):
    @given(kind=st.sampled_from(["year", "show"]),
           method=st.sampled_from(["GET", "POST", "PUT", "PATCH"]),
           number=st.integers(100000, 200000))
    def check(kind, method, number):
        response = client.open(
            f"/api/{kind}/missing-{number}/metadata", method=method,
            json={}, headers=alice_headers,
        )
        assert response.status_code == 404

    check()


def test_special_and_national_final_keys_address_the_same_metadata(client, db, alice_headers):
    db.execute(
        """INSERT INTO year (id, status, special_name, special_short_name)
           VALUES (-2031, 'open', 'Metadata Special', 'metadata-special')
           ON CONFLICT DO NOTHING"""
    )
    nf_id = db.execute(
        """INSERT INTO national_final (year_id, owner_id, short_name, name)
           VALUES (-2031, 2, 'selection-test', 'Selection') RETURNING id"""
    ).fetchone()["id"]
    shows = {}
    for key, national_final_id in (("f", None), ("selection-test-f", nf_id)):
        shows[key] = db.execute(
            """INSERT INTO show (year_id, show_type, national_final_id)
               VALUES (-2031, 'f', %s) RETURNING id""",
            (national_final_id,),
        ).fetchone()["id"]
    db.commit()

    @settings(max_examples=20, deadline=None)
    @given(
        show=st.sampled_from(list(shows)), special=st.booleans(), enabled=st.booleans(),
        slug=st.lists(st.text(alphabet="abcdef", min_size=1, max_size=5),
                      min_size=1, max_size=3).map("-".join),
        method=st.sampled_from(["GET", "POST", "PUT", "PATCH"]),
    )
    def check(show, special, enabled, slug, method):
        year = -2031 if special else 2025
        db.execute("UPDATE year SET special_short_name = %s WHERE id = -2031", (slug,))
        db.execute("UPDATE national_final SET year_id = %s WHERE id = %s", (year, nf_id))
        db.execute("UPDATE show SET year_id = %s WHERE id = ANY(%s)",
                   (year, list(shows.values())))
        db.commit()
        discovered = client.get(f"/api/show?year={year}").get_json()["result"]
        key = next(item["key"] for item in discovered if item["id"] == shows[show])
        path = f"/api/show/{key}/metadata"
        opening = {"opening": f"{MEDIA}/{show}.mov" if enabled else None}
        response = client.put(path, json=opening, headers=alice_headers)
        assert response.status_code == 200
        assert client.get(path).get_json()["result"] == opening
        assert db.execute("SELECT metadata FROM show WHERE id = %s",
                          (shows[show],)).fetchone()["metadata"] == opening
        response = client.open(f"/api/show/{shows[show]}/metadata", method=method,
                               json={}, headers=alice_headers)
        assert response.status_code == 404
        assert client.get(path).get_json()["result"] == opening

    check()


def test_metadata_forms_save_typed_values_and_return_to_management(client, db, login):
    from werkzeug.datastructures import MultiDict

    login(1)
    show_id = _show(db)
    db.execute(
        """INSERT INTO year (id, special_name, special_short_name)
           VALUES (-2032, 'Form Special', 'form-special') ON CONFLICT DO NOTHING"""
    )
    db.commit()

    @settings(max_examples=20, deadline=None)
    @given(
        opening=st.booleans(), countdown=st.booleans(), custom_opening=st.booleans(),
        intervals=st.lists(st.integers(0, 99), max_size=8), special=st.booleans(),
        multipart=st.booleans(), return_to=st.booleans(),
        method=st.sampled_from(["POST", "PUT"]),
    )
    def check(opening, countdown, custom_opening, intervals, special, multipart, return_to, method):
        year = -2032 if special else 2025
        db.execute("UPDATE show SET year_id = %s WHERE id = %s", (year, show_id))
        db.commit()
        year_form = MultiDict()
        if opening:
            year_form.add("opening", "on")
        if countdown:
            year_form.add("countdown", "YES")
        urls = [f"{MEDIA}/{i}.mov" for i in intervals]
        show_form = MultiDict([
            ("opening", f"  {MEDIA}/opening.mov  " if custom_opening else ""),
            ("intervals", "\r\n".join(urls)),
            ("intervals", "\n  \n"),
        ])
        expected_show = {
            "opening": f"{MEDIA}/opening.mov" if custom_opening else None, "intervals": urls
        }
        show_key = f"{'form-special' if special else year}-f"
        for path, form, expected in (
            (f"/api/year/{year}/metadata", year_form,
             {key: True for key, checked in (("opening", opening), ("countdown", countdown))
              if checked}),
            (f"/api/show/{show_key}/metadata", show_form, expected_show),
        ):
            if return_to:
                form.add("return_to", "manage")
            response = client.open(
                path, method=method, data=form,
                content_type="multipart/form-data" if multipart
                else "application/x-www-form-urlencoded",
            )
            if return_to:
                assert response.status_code == 303
                assert response.location == (
                    "/admin/manage/special/form-special" if special else "/admin/manage/2025"
                )
                page = client.get(response.location, headers={"Accept": "text/html"})
                assert page.status_code == 200
            else:
                assert response.status_code == 200
                assert response.get_json()["result"] == expected
            assert client.get(path).get_json()["result"] == expected

    try:
        check()
    finally:
        db.execute("UPDATE year SET metadata = '{}' WHERE id IN (2025, -2032)")
        db.commit()


def test_metadata_forms_reject_non_admins_and_invalid_values(client, db, login):
    _show(db)

    @settings(max_examples=12, deadline=None)
    @given(admin=st.booleans(), year=st.booleans(),
           value=st.text(alphabet="abcdef", min_size=1, max_size=12))
    def check(admin, year, value):
        login(1 if admin else 2)
        path = "/api/year/2025/metadata" if year else "/api/show/2025-f/metadata"
        before = client.get(path).get_json()
        response = client.post(path, data={"opening": value, "return_to": "manage"},
                               headers={"Accept": "text/html"})
        assert response.status_code == (400 if admin else 403)
        assert client.get(path).get_json() == before

    check()
