import string

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.song_revisions import set_song_status

JSON = {"Accept": "application/json"}


def _song(db, country: str, title: str, *, entry_number: int = 1) -> int:
    with db.cursor() as cursor:
        song_id = cursor.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               VALUES (%s, 2024, %s) RETURNING id""",
            (country, entry_number),
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id, video_link
               ) VALUES (
                   %s, 1, %s, test_artist_credit('Test Artist'), %s
               )""",
            (song_id, title, f"https://media.world-stage.org/{song_id}.mp4"),
        )
        set_song_status(cursor, song_id, changed_by=1, is_placeholder=False)
    db.commit()
    return song_id


def _create_playlist(client, db, name: str = "Favourites") -> int:
    response = client.post("/member/playlist", data={"name": name}, headers=JSON)
    assert response.status_code == 302
    return db.execute(
        """SELECT id FROM custom_playlist
           WHERE owner_id = 2 AND name = %s ORDER BY id DESC LIMIT 1""",
        (name,),
    ).fetchone()["id"]


def test_playlist_management_requires_authentication_and_ownership(client, db, login):
    @given(path=st.sampled_from(["/member/playlist", "/member/playlist/edit?playlist_id=1"]))
    def anonymous_property(path):
        response = client.get(path, headers=JSON)
        assert response.status_code == 302
        assert response.location.endswith("/login")

    anonymous_property()
    login(2)
    playlist_id = _create_playlist(client, db)
    song_id = _song(db, "ES", "Owned song")

    @given(operation=st.sampled_from(["read", "rename", "add", "order"]))
    def ownership_property(operation):
        client.delete_cookie("session")
        login(3)
        if operation == "read":
            response = client.get(f"/member/playlist/edit?playlist_id={playlist_id}", headers=JSON)
        elif operation == "rename":
            response = client.post(
                f"/member/playlist/{playlist_id}/rename",
                data={"name": "Not mine"},
                headers=JSON,
            )
        elif operation == "add":
            response = client.post(
                f"/member/playlist/{playlist_id}/songs",
                data={"song_id": song_id},
                headers=JSON,
            )
        else:
            response = client.post(
                f"/member/playlist/{playlist_id}/order",
                json={"song_ids": []},
                headers=JSON,
            )
        assert response.status_code == 404
        assert (
            db.execute("SELECT name FROM custom_playlist WHERE id = %s", (playlist_id,)).fetchone()[
                "name"
            ]
            == "Favourites"
        )
        assert (
            db.execute(
                "SELECT COUNT(*) AS count FROM custom_playlist_song WHERE playlist_id = %s",
                (playlist_id,),
            ).fetchone()["count"]
            == 0
        )

    ownership_property()


def test_playlist_names_are_trimmed_and_bounded(client, db, login):
    login(2)
    playlist_id = _create_playlist(client, db)
    alphabet = string.ascii_letters + string.digits + " _-"

    @settings(max_examples=20)
    @given(raw_name=st.text(alphabet=alphabet, min_size=0, max_size=105))
    def property_test(raw_name):
        before = db.execute(
            "SELECT name FROM custom_playlist WHERE id = %s", (playlist_id,)
        ).fetchone()["name"]
        response = client.post(
            f"/member/playlist/{playlist_id}/rename",
            data={"name": raw_name},
            headers=JSON,
        )
        normalized = raw_name.strip()
        valid = 1 <= len(normalized) <= 100
        assert response.status_code == (302 if valid else 400)
        after = db.execute(
            "SELECT name FROM custom_playlist WHERE id = %s", (playlist_id,)
        ).fetchone()["name"]
        assert after == (normalized if valid else before)

    property_test()


def test_playlist_search_matches_the_requested_catalog_filters(client, db, login):
    songs = {
        _song(db, "ES", "First Spanish song", entry_number=1): "ES",
        _song(db, "ES", "Second Spanish song", entry_number=2): "ES",
        _song(db, "FR", "French song", entry_number=1): "FR",
    }
    login(2)
    playlist_id = _create_playlist(client, db)

    @given(
        country=st.sampled_from(["", "ES", "FR"]),
        year=st.sampled_from(["", "2024", "not-a-year"]),
    )
    def property_test(country, year):
        response = client.get(
            f"/member/playlist/edit?playlist_id={playlist_id}&country={country}&year={year}",
            headers=JSON,
        )
        if year == "not-a-year":
            assert response.status_code == 400
            return

        assert response.status_code == 200
        result = response.get_json()["search_results"]
        expected = {
            song_id
            for song_id, song_country in songs.items()
            if (country or year) and (not country or song_country == country)
        }
        assert {song["id"] for song in result} == expected
        assert all(song["in_playlist"] is False for song in result)

    property_test()


def test_playlist_membership_is_an_idempotent_ordered_set(client, db, login):
    song_ids = [
        _song(db, "ES", "First song", entry_number=1),
        _song(db, "FR", "Second song", entry_number=1),
        _song(db, "US", "Third song", entry_number=1),
    ]
    login(2)
    playlist_id = _create_playlist(client, db)

    @settings(max_examples=12, deadline=None)
    @given(
        insertion=st.permutations(song_ids),
        final_order=st.permutations(song_ids),
        duplicate_index=st.integers(min_value=0, max_value=2),
        remove_count=st.integers(min_value=0, max_value=2),
        postcards=st.booleans(),
    )
    def property_test(insertion, final_order, duplicate_index, remove_count, postcards):
        db.execute("DELETE FROM custom_playlist_song WHERE playlist_id = %s", (playlist_id,))
        db.commit()

        for song_id in insertion:
            response = client.post(
                f"/member/playlist/{playlist_id}/songs",
                data={"song_id": song_id},
                headers=JSON,
            )
            assert response.status_code == 200
            assert response.get_json()["result"]["added"] is True

        duplicate = client.post(
            f"/member/playlist/{playlist_id}/songs",
            data={"song_id": insertion[duplicate_index]},
            headers=JSON,
        )
        assert duplicate.get_json()["result"]["added"] is False

        reordered = client.post(
            f"/member/playlist/{playlist_id}/order",
            json={"song_ids": final_order},
            headers=JSON,
        )
        assert reordered.status_code == 200

        removed = set(final_order[:remove_count])
        for song_id in removed:
            response = client.post(
                f"/member/playlist/{playlist_id}/songs/{song_id}/remove",
                headers=JSON,
            )
            assert response.status_code == 302
        expected = [song_id for song_id in final_order if song_id not in removed]

        response = client.get(
            f"/playlist/{playlist_id}?postcards={'true' if postcards else 'false'}",
            headers=JSON,
        )
        assert response.status_code == 200
        entries = response.get_json()["entries"]
        played_songs = [entry for entry in entries if entry["kind"] == "song"]
        assert [entry["id"] for entry in played_songs] == expected
        assert len(entries) == len(expected) * (2 if postcards else 1)
        assert all(entry["shuffleable"] is True for entry in entries)
        for song in played_songs:
            group = [entry for entry in entries if entry["shuffle_group"] == song["shuffle_group"]]
            assert len(group) == (2 if postcards else 1)

    property_test()
