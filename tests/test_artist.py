import uuid


def _create_artist_entry(client, headers, *, name="Canonical Artist"):
    response = client.post(
        "/api/song",
        json={
            "year": 2025,
            "country": "US",
            "title": "Artist Route Song",
            "artist": None,
            "artists": [{"full_name": name, "stage_name": None}],
            "sources": "http://example.com",
            "languages": [20],
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.get_json()["result"]


def _login(client, db, user_id):
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO session (user_id, session_id, expires_at)
               VALUES (%s, %s, CURRENT_TIMESTAMP + '1 day')""",
            (user_id, session_id),
        )
    db.commit()
    client.set_cookie("session", session_id)


def test_artist_index_and_details_list_associated_entries(
    client, db, bob_headers
):
    try:
        _create_artist_entry(client, bob_headers)
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'ongoing' WHERE id = 2025")
        db.commit()

        headers = {"Accept": "text/html"}
        index = client.get("/artist", headers=headers)
        details = client.get("/artist/Canonical%20Artist", headers=headers)

        assert index.status_code == 200
        assert b"Canonical Artist" in index.data
        assert details.status_code == 200
        assert b"Artist Route Song" in details.data
        assert b"United States" in details.data
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'open' WHERE id = 2025")
        db.commit()


def test_artist_from_open_year_is_hidden(client, bob_headers):
    _create_artist_entry(client, bob_headers)
    headers = {"Accept": "text/html"}

    index = client.get("/artist", headers=headers)
    details = client.get("/artist/Canonical%20Artist", headers=headers)

    assert index.status_code == 200
    assert b"Canonical Artist" not in index.data
    assert details.status_code == 404


def test_admin_can_edit_artist_and_native_name(client, db, bob_headers):
    song = _create_artist_entry(client, bob_headers)
    _login(client, db, 1)

    response = client.post(
        "/artist/Canonical%20Artist/edit",
        data={
            "full_name": "Renamed Artist",
            "native_name": "Переименованный артист",
            "number": "1",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/artist/Renamed%20Artist")
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT full_name, native_name FROM artist WHERE full_name = %s",
            ("Renamed Artist",),
        )
        assert cursor.fetchone() == {
            "full_name": "Renamed Artist",
            "native_name": "Переименованный артист",
        }
        cursor.execute("SELECT artist FROM current_song WHERE id = %s", (song["id"],))
        assert cursor.fetchone()["artist"] == "Renamed Artist"


def test_regular_user_cannot_edit_artist(client, db, bob_headers):
    _create_artist_entry(client, bob_headers)
    _login(client, db, 2)

    response = client.get("/artist/Canonical%20Artist/edit")

    assert response.status_code == 403
