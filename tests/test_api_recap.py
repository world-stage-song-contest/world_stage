def _result(response):
    return response.get_json()["result"]


def _seed_recap_data(db, *, show_name="API Recap", short_name="api"):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO show (year_id, show_name, short_name)
            VALUES (2025, %s, %s)
            RETURNING id
            """,
            (show_name, short_name),
        )
        show_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, submitter_id, artist, title)
            VALUES ('US', 2025, 1, 'API Artist', 'API Song')
            RETURNING id
            """
        )
        song_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, 1)",
            (song_id, show_id),
        )
    db.commit()
    return song_id


def test_recap_api_returns_recap_data(client, db):
    _seed_recap_data(db)

    response = client.get(
        "/api/recap",
        query_string={"type": "show", "show": "2025-api"},
    )

    assert response.status_code == 200
    assert _result(response) == [
        {
            "year": 2025,
            "submitter": "alice",
            "show": "2025api",
            "ro": 1,
            "cc": "us",
            "country": "United States",
            "artist": "API Artist",
            "title": "API Song",
            "snippet_start": 50,
            "snippet_end": 70,
            "type": "video",
        }
    ]


def test_recap_api_preserves_configured_snippet_times(client, db):
    song_id = _seed_recap_data(db, show_name="Timed API Recap", short_name="timed")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE song SET snippet_start = 0, snippet_end = 30 WHERE id = %s",
            (song_id,),
        )
    db.commit()

    response = client.get(
        "/api/recap",
        query_string={"type": "show", "show": "2025-timed"},
    )

    assert response.status_code == 200
    assert _result(response)[0]["snippet_start"] == 0
    assert _result(response)[0]["snippet_end"] == 30


def test_recap_api_is_public(client, db):
    _seed_recap_data(db, show_name="Public API Recap", short_name="public-api")

    response = client.get(
        "/api/recap",
        query_string={"type": "show", "show": "2025-public-api"},
    )

    assert response.status_code == 200
    assert _result(response)[0]["title"] == "API Song"


def test_recap_api_etag_tracks_exported_values(client, db):
    song_id = _seed_recap_data(db, show_name="ETag API Recap", short_name="etag")
    query = {"type": "show", "show": "2025-etag"}

    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE song_audit_log
            SET changed_at = '2030-01-02 03:04:05+00'
            WHERE song_id = %s
            """,
            (song_id,),
        )
    db.commit()

    initial = client.get("/api/recap", query_string=query)
    repeated = client.get("/api/recap", query_string=query)
    not_modified = client.get(
        "/api/recap",
        query_string=query,
        headers={"If-None-Match": initial.headers["ETag"]},
    )
    if_match = client.get(
        "/api/recap",
        query_string=query,
        headers={"If-Match": initial.headers["ETag"]},
    )
    if_match_failed = client.get(
        "/api/recap",
        query_string=query,
        headers={"If-Match": '"stale"'},
    )
    if_modified_since = client.get(
        "/api/recap",
        query_string=query,
        headers={"If-Modified-Since": initial.headers["Last-Modified"]},
    )
    if_unmodified_since = client.get(
        "/api/recap",
        query_string=query,
        headers={"If-Unmodified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"},
    )

    with db.cursor() as cur:
        cur.execute("UPDATE song SET notes = 'Not part of recap data' WHERE id = %s", (song_id,))
    db.commit()
    unrelated_change = client.get("/api/recap", query_string=query)

    with db.cursor() as cur:
        cur.execute("UPDATE song SET title = 'Changed API Song' WHERE id = %s", (song_id,))
    db.commit()
    exported_change = client.get("/api/recap", query_string=query)
    stale_etag = client.get(
        "/api/recap",
        query_string=query,
        headers={"If-None-Match": initial.headers["ETag"]},
    )

    assert initial.headers["ETag"] == repeated.headers["ETag"]
    assert initial.headers["Last-Modified"] == "Wed, 02 Jan 2030 03:04:05 GMT"
    assert not_modified.status_code == 304
    assert not_modified.data == b""
    assert if_match.status_code == 200
    assert if_match_failed.status_code == 412
    assert if_modified_since.status_code == 304
    assert if_unmodified_since.status_code == 412
    assert initial.headers["ETag"] == unrelated_change.headers["ETag"]
    assert initial.headers["ETag"] != exported_change.headers["ETag"]
    assert stale_etag.status_code == 200
    assert stale_etag.headers["ETag"] == exported_change.headers["ETag"]


def test_recap_api_country_accepts_codes_and_names(client, db):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, submitter_id, artist, title)
            VALUES ('US', 2024, 1, 'Country Artist', 'Country Song')
            """
        )
    db.commit()

    for selection in ("US", "us", "USA", "United States", "united states"):
        response = client.get(
            "/api/recap",
            query_string={"type": "country", "show": selection},
        )

        assert response.status_code == 200
        assert _result(response)[0]["title"] == "Country Song"


def test_recap_api_excludes_specials_unless_requested(client, db):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO year (id, status, host_id, special_name, special_short_name)
            VALUES (-1, 'closed', 'US', 'Special Recap', 'sr')
            """
        )
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, submitter_id, artist, title)
            VALUES ('US', -1, 1, 'Special Artist', 'Special Song')
            """
        )
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, submitter_id, artist, title)
            VALUES ('US', 2024, 1, 'Regular Artist', 'Regular Song')
            """
        )
    db.commit()

    default_response = client.get(
        "/api/recap", query_string={"type": "country", "show": "US"}
    )
    explicit_false_response = client.get(
        "/api/recap",
        query_string={"type": "country", "show": "US", "specials": "false"},
    )
    enabled_response = client.get(
        "/api/recap",
        query_string={"type": "country", "show": "US", "specials": "true"},
    )
    only_response = client.get(
        "/api/recap",
        query_string={"type": "country", "show": "US", "specials": "only"},
    )

    assert [row["title"] for row in _result(default_response)] == ["Regular Song"]
    assert [row["title"] for row in _result(explicit_false_response)] == ["Regular Song"]
    assert {row["title"] for row in _result(enabled_response)} == {"Regular Song", "Special Song"}
    assert [row["title"] for row in _result(only_response)] == ["Special Song"]


def test_recap_api_validates_the_request(client):
    response = client.get("/api/recap")

    assert response.status_code == 400
    assert response.get_json()["error"]["description"] == (
        "type must be show, year, country, or submitter"
    )
