import unicodedata
from urllib.parse import quote

from hypothesis import given, settings
from hypothesis import strategies as st


def _create_artist_entry(client, headers, *, name="Canonical Artist", stage_name=None):
    response = client.post(
        "/api/song",
        json={
            "year": 2025,
            "country": "US",
            "title": "Artist Route Song",
            "artist": None,
            "artists": [{"full_name": name, "stage_name": stage_name}],
            "sources": "http://example.com",
            "languages": [20],
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.get_json()["result"]


def test_admin_can_edit_artist_and_native_name(client, db, bob_headers, login):
    song = _create_artist_entry(client, bob_headers)
    login(1)

    @settings(max_examples=20)
    @given(
        suffix=st.text(alphabet=st.characters(categories=("L", "N")), min_size=1, max_size=12),
        native_name=st.one_of(
            st.none(),
            st.text(
                alphabet=st.characters(categories=("L", "N", "Zs")),
                min_size=1,
                max_size=30,
            ),
        ),
    )
    def property_test(suffix, native_name):
        current_name = db.execute(
            "SELECT artist FROM current_song WHERE id = %s", (song["id"],)
        ).fetchone()["artist"]
        requested_name = f"Property Artist {suffix}"
        normalized_name = unicodedata.normalize("NFC", requested_name)

        response = client.post(
            f"/artist/{quote(current_name, safe='')}/edit",
            data={
                "full_name": requested_name,
                "native_name": native_name or "",
                "number": "1",
            },
        )

        assert response.status_code == 302
        normalized_native_name = (
            unicodedata.normalize("NFC", native_name.strip()) if native_name else ""
        )
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT full_name, native_name FROM artist WHERE full_name = %s",
                (normalized_name,),
            )
            assert cursor.fetchone() == {
                "full_name": normalized_name,
                "native_name": normalized_native_name or None,
            }
            cursor.execute("SELECT artist FROM current_song WHERE id = %s", (song["id"],))
            assert cursor.fetchone()["artist"] == normalized_name

    property_test()


def test_regular_user_cannot_edit_artist(client, db, bob_headers, login):
    _create_artist_entry(client, bob_headers)

    @given(user_id=st.sampled_from([2, 3]))
    def property_test(user_id):
        login(user_id)
        response = client.get("/artist/Canonical%20Artist/edit")

        assert response.status_code == 403

    property_test()
