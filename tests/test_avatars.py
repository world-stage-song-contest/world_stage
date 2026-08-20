from io import BytesIO

from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image


def _image_file(size: tuple[int, int], image_format: str = "PNG") -> BytesIO:
    output = BytesIO()
    Image.new("RGB", size, "#4c78a8").save(output, format=image_format)
    output.seek(0)
    return output


def _remove_avatar(db, user_id: int = 2):
    db.execute("DELETE FROM account_avatar WHERE account_id = %s", (user_id,))
    db.commit()


def test_default_avatars_are_deterministic_cacheable_and_user_specific(client):
    @given(user_id=st.sampled_from([1, 2, 3]))
    def property_test(user_id):
        first = client.get(f"/avatars/{user_id}")
        second = client.get(f"/avatars/{user_id}")

        assert first.status_code == 200
        assert first.content_type == "image/svg+xml"
        assert first.data == second.data
        assert first.headers["X-Content-Type-Options"] == "nosniff"
        assert "must-revalidate" in first.headers["Cache-Control"]
        assert first.headers["ETag"]
        assert (
            client.get(
                f"/avatars/{user_id}", headers={"If-None-Match": first.headers["ETag"]}
            ).status_code
            == 304
        )

        other_id = 1 if user_id != 1 else 2
        assert first.data != client.get(f"/avatars/{other_id}").data

    property_test()


def test_avatar_upload_round_trips_generated_images_and_can_be_removed(client, db, login):
    login(2)

    @settings(max_examples=20, deadline=None)
    @given(
        width=st.integers(min_value=1, max_value=512),
        height=st.integers(min_value=1, max_value=512),
        image_format=st.sampled_from(["PNG", "JPEG"]),
    )
    def property_test(width, height, image_format):
        try:
            response = client.post(
                "/settings/avatar",
                data={
                    "action": "upload",
                    "avatar": (
                        _image_file((width, height), image_format),
                        f"avatar.{image_format.lower()}",
                    ),
                },
                content_type="multipart/form-data",
                headers={"Accept": "text/html"},
            )
            assert response.status_code == 302

            avatar = db.execute(
                """SELECT image_data, media_type, width, height
                   FROM account_avatar WHERE account_id = 2"""
            ).fetchone()
            assert avatar["media_type"] == "image/png"
            assert (avatar["width"], avatar["height"]) == (width, height)
            with Image.open(BytesIO(avatar["image_data"])) as image:
                assert image.format == "PNG"
                assert image.size == (width, height)

            served = client.get("/avatars/2")
            assert served.content_type == "image/png"
            assert served.data == avatar["image_data"]

            removed = client.post(
                "/settings/avatar",
                data={"action": "remove"},
                headers={"Accept": "text/html"},
            )
            assert removed.status_code == 302
            assert (
                db.execute("SELECT 1 FROM account_avatar WHERE account_id = 2").fetchone() is None
            )
        finally:
            _remove_avatar(db)

    property_test()


def test_avatar_upload_rejects_generated_invalid_images(client, db, login):
    login(2)
    invalid_uploads = st.one_of(
        st.tuples(
            st.integers(min_value=513, max_value=800),
            st.integers(min_value=1, max_value=20),
        ).map(lambda size: (_image_file(size), "oversized.png")),
        st.binary(max_size=100).map(
            lambda payload: (BytesIO(b"not-an-image\0" + payload), "invalid.png")
        ),
    )

    @settings(max_examples=20, deadline=None)
    @given(upload=invalid_uploads)
    def property_test(upload):
        try:
            response = client.post(
                "/settings/avatar",
                data={"action": "upload", "avatar": upload},
                content_type="multipart/form-data",
                headers={"Accept": "text/html"},
            )

            assert response.status_code == 400
            assert (
                db.execute("SELECT 1 FROM account_avatar WHERE account_id = 2").fetchone() is None
            )
        finally:
            _remove_avatar(db)

    property_test()
