import uuid
from urllib.parse import parse_qs, urlparse

from world_stage.utils.song_revisions import set_song_status

JSON = {"Accept": "application/json"}


def _login(client, db, user_id: int = 2):
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP + '1 day')
            """,
            (user_id, session_id),
        )
    db.commit()
    client.set_cookie("session", session_id)


def _song(
    db, country: str, title: str, *, year: int = 2024, entry_number: int = 1
) -> int:
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO song (country_id, year_id, entry_number)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (country, year, entry_number),
        )
        song_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO song_data (song_id, submitter_id, title, artist, video_link)
            VALUES (%s, 1, %s, 'Test Artist', %s)
            """,
            (song_id, title, f"https://media.world-stage.org/{song_id}.mp4"),
        )
        set_song_status(cursor, song_id, changed_by=1, is_placeholder=False)
    db.commit()
    return song_id


def _create_playlist(client, name: str = "Favourites") -> int:
    response = client.post("/member/playlist", data={"name": name}, headers=JSON)
    assert response.status_code == 302
    assert urlparse(response.location).path == "/member/playlist/edit"
    return int(parse_qs(urlparse(response.location).query)["playlist_id"][0])


def test_playlist_pages_require_login(client):
    assert client.get("/member/playlist", headers=JSON).location.endswith("/login")
    response = client.get("/member/playlist/edit?playlist_id=1", headers=JSON)
    assert response.location.endswith("/login")


def test_user_creates_searches_and_plays_playlist(client, db):
    spanish_song = _song(db, "ES", "Spanish song")
    _song(db, "FR", "French song")
    _login(client, db)
    playlist_id = _create_playlist(client)

    response = client.get(
        f"/member/playlist/edit?playlist_id={playlist_id}&country=ES",
        headers=JSON,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["selected_country"] == "ES"
    assert [song["id"] for song in data["search_results"]] == [spanish_song]
    assert [song["title"] for song in data["search_results"]] == ["Spanish song"]

    response = client.post(
        f"/member/playlist/{playlist_id}/songs",
        data={"song_id": spanish_song},
        headers=JSON,
    )
    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["playlist_id"] == playlist_id
    assert result["song_id"] == spanish_song
    assert result["added"] is True
    assert result["song"] == {
        "id": spanish_song,
        "cc": "es",
        "country": "Spain",
        "title": "Spanish song",
        "artist": "Test Artist",
        "year": 2024,
        "details_url": "/country/es/2024/1",
        "remove_url": f"/member/playlist/{playlist_id}/songs/{spanish_song}/remove",
    }

    # Adding the same song twice is intentionally idempotent.
    duplicate = client.post(
        f"/member/playlist/{playlist_id}/songs",
        data={"song_id": spanish_song},
        headers=JSON,
    )
    assert duplicate.get_json()["result"]["added"] is False
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM custom_playlist_song WHERE playlist_id = %s",
            (playlist_id,),
        )
        assert cursor.fetchone()["count"] == 1

    refreshed = client.get(
        f"/member/playlist/edit?playlist_id={playlist_id}&country=ES",
        headers=JSON,
    )
    assert refreshed.get_json()["search_results"][0]["in_playlist"] is True

    response = client.get(f"/playlist/{playlist_id}", headers=JSON)
    assert response.status_code == 200
    data = response.get_json()
    assert data["collection_title"] == "Favourites"
    assert [(entry["id"], entry["title"]) for entry in data["entries"]] == [
        (spanish_song, "Spanish song")
    ]
    assert data["entries"][0]["shuffleable"] is True

    with_postcard = client.get(
        f"/playlist/{playlist_id}?postcards=true", headers=JSON
    ).get_json()["entries"]
    assert [entry["kind"] for entry in with_postcard] == ["postcard", "song"]
    assert len({entry["shuffle_group"] for entry in with_postcard}) == 1
    assert all(entry["shuffleable"] is True for entry in with_postcard)


def test_playlist_ownership_is_enforced(client, db):
    _login(client, db, 2)
    playlist_id = _create_playlist(client, "Bob's list")

    client.delete_cookie("session")
    _login(client, db, 3)
    response = client.get(f"/member/playlist/edit?playlist_id={playlist_id}", headers=JSON)
    assert response.status_code == 404
    assert response.get_json()["error"] == "Playlist not found"

    # Public listing and playback do not grant editing access.
    response = client.get(f"/playlist/{playlist_id}", headers=JSON)
    assert response.status_code == 400
    assert response.get_json()["error"] == "This playlist is empty"


def test_multiple_filtered_songs_can_be_added_without_leaving_results(client, db):
    first_song = _song(db, "ES", "First Spanish song", entry_number=1)
    second_song = _song(db, "ES", "Second Spanish song", entry_number=2)
    _login(client, db)
    playlist_id = _create_playlist(client)

    results = client.get(
        f"/member/playlist/edit?playlist_id={playlist_id}&country=ES",
        headers=JSON,
    ).get_json()["search_results"]
    assert [song["id"] for song in results] == [first_song, second_song]
    assert all(song["in_playlist"] is False for song in results)

    for song_id in (first_song, second_song):
        response = client.post(
            f"/member/playlist/{playlist_id}/songs",
            data={"song_id": song_id},
            headers=JSON,
        )
        assert response.status_code == 200
        assert response.get_json()["result"]["added"] is True

    refreshed = client.get(
        f"/member/playlist/edit?playlist_id={playlist_id}&country=ES",
        headers=JSON,
    ).get_json()["search_results"]
    assert [song["id"] for song in refreshed] == [first_song, second_song]
    assert all(song["in_playlist"] is True for song in refreshed)


def test_user_can_reorder_playlist_cards(client, db):
    first_song = _song(db, "ES", "First song")
    second_song = _song(db, "FR", "Second song")
    _login(client, db)
    playlist_id = _create_playlist(client)
    for song_id in (first_song, second_song):
        client.post(
            f"/member/playlist/{playlist_id}/songs",
            data={"song_id": song_id},
            headers=JSON,
        )

    response = client.post(
        f"/member/playlist/{playlist_id}/order",
        json={"song_ids": [second_song, first_song]},
        headers=JSON,
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["song_ids"] == [second_song, first_song]

    form_response = client.post(
        f"/member/playlist/{playlist_id}/order",
        data={"song_id": [second_song, first_song]},
        headers=JSON,
    )
    assert form_response.status_code == 302
    assert form_response.location == (
        f"/member/playlist/edit?playlist_id={playlist_id}"
    )

    player = client.get(f"/playlist/{playlist_id}", headers=JSON)
    assert [entry["id"] for entry in player.get_json()["entries"]] == [
        second_song,
        first_song,
    ]

    invalid = client.post(
        f"/member/playlist/{playlist_id}/order",
        json={"song_ids": [first_song]},
        headers=JSON,
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "Invalid playlist order"


def test_show_recap_is_fixed_and_postcards_are_linked(client, db):
    first_song = _song(db, "ES", "First song")
    second_song = _song(db, "FR", "Second song")
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(MAX(show_number), 0) + 1 AS number
            FROM show WHERE year_id = 2024 AND show_type = 'sf'
            """
        )
        show_number = cursor.fetchone()["number"]
        cursor.execute(
            """
            INSERT INTO show (year_id, show_type, show_number)
            VALUES (2024, 'sf', %s) RETURNING id
            """,
            (show_number,),
        )
        show_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO song_show (song_id, show_id, running_order)
            VALUES (%s, %s, 1), (%s, %s, 2)
            """,
            (first_song, show_id, second_song, show_id),
        )
    db.commit()

    response = client.get(
        f"/year/2024/sf{show_number}/play?postcards=true", headers=JSON
    )
    assert response.status_code == 200
    entries = response.get_json()["entries"]
    assert entries[-1]["kind"] == "recap"
    assert entries[-1]["shuffleable"] is False
    assert [entry["kind"] for entry in entries[:-1]] == [
        "postcard",
        "song",
        "postcard",
        "song",
    ]
    assert entries[0]["shuffle_group"] == entries[1]["shuffle_group"]
    assert entries[2]["shuffle_group"] == entries[3]["shuffle_group"]
    assert entries[0]["shuffle_group"] != entries[2]["shuffle_group"]


def test_song_details_offer_users_their_playlists(client, db):
    _song(db, "ES", "Details song")
    _login(client, db)
    playlist_id = _create_playlist(client, "Road trip")

    response = client.get("/country/es/2024", headers=JSON)
    assert response.status_code == 200
    assert response.get_json()["custom_playlists"] == [
        {"id": playlist_id, "name": "Road trip"}
    ]


def test_user_pages_link_to_a_dedicated_playlist_page(client, db):
    song_id = _song(db, "ES", "Public song")
    _login(client, db)
    playlist_id = _create_playlist(client, "Road trip")
    client.post(
        f"/member/playlist/{playlist_id}/songs",
        data={"song_id": song_id},
        headers=JSON,
    )

    playlist_page = client.get("/user/bob/playlist", headers=JSON)
    assert playlist_page.status_code == 200
    assert playlist_page.get_json()["playlists"] == [
        {"id": playlist_id, "name": "Road trip"}
    ]

    directory = client.get("/user", headers=JSON)
    bob = next(user for user in directory.get_json()["users"]["B"] if user["id"] == 2)
    assert bob == {"id": 2, "username": "bob"}

    profile = client.get("/user/bob", headers=JSON)
    assert profile.status_code == 200
    assert profile.get_json()["username"] == "bob"

    client.delete_cookie("session")
    public_index = client.get("/playlist", headers=JSON)
    assert public_index.status_code == 200
    assert public_index.get_json()["groups"] == [
        {
            "user_id": 2,
            "username": "bob",
            "playlists": [{"id": playlist_id, "name": "Road trip"}],
        }
    ]

    playback = client.get(f"/playlist/{playlist_id}", headers=JSON)
    assert playback.status_code == 200
    assert [(entry["id"], entry["title"]) for entry in playback.get_json()["entries"]] == [
        (song_id, "Public song")
    ]

    assert client.get(f"/user/bob/playlist/{playlist_id}", headers=JSON).status_code == 404
    assert (
        client.get(f"/member/playlist/{playlist_id}/play", headers=JSON).status_code
        == 404
    )
