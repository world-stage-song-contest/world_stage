import uuid
from io import BytesIO

from PIL import Image


def _login(client, db, user_id: int):
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


def _image_file(size: tuple[int, int], image_format: str = "PNG") -> BytesIO:
    output = BytesIO()
    Image.new("RGB", size, "#4c78a8").save(output, format=image_format)
    output.seek(0)
    return output


def test_default_avatar_is_deterministic_and_cache_revalidated(client):
    first = client.get("/avatars/2")
    second = client.get("/avatars/2")
    other_user = client.get("/avatars/3")

    assert first.status_code == 200
    assert first.content_type == "image/svg+xml"
    assert first.data == second.data
    assert first.data != other_user.data
    assert b'<svg xmlns="http://www.w3.org/2000/svg"' in first.data
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert "must-revalidate" in first.headers["Cache-Control"]
    assert first.headers["ETag"]

    conditional = client.get("/avatars/2", headers={"If-None-Match": first.headers["ETag"]})
    assert conditional.status_code == 304
    assert client.get("/avatars/999999").status_code == 404


def test_user_can_upload_and_remove_avatar(client, db):
    _login(client, db, 2)

    response = client.post(
        "/settings/avatar",
        data={
            "action": "upload",
            "avatar": (_image_file((128, 96), "JPEG"), "avatar.jpg"),
        },
        content_type="multipart/form-data",
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/settings?avatar=updated")
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT image_data, media_type, width, height
            FROM account_avatar
            WHERE account_id = 2
            """
        )
        avatar = cursor.fetchone()
    assert avatar["media_type"] == "image/png"
    assert (avatar["width"], avatar["height"]) == (128, 96)
    with Image.open(BytesIO(avatar["image_data"])) as image:
        assert image.format == "PNG"
        assert image.size == (128, 96)

    served = client.get("/avatars/2")
    assert served.status_code == 200
    assert served.content_type == "image/png"
    assert served.data == avatar["image_data"]

    response = client.post(
        "/settings/avatar",
        data={"action": "remove"},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/settings?avatar=removed")
    with db.cursor() as cursor:
        cursor.execute("SELECT 1 FROM account_avatar WHERE account_id = 2")
        assert cursor.fetchone() is None
    assert client.get("/avatars/2").content_type == "image/svg+xml"


def test_avatar_upload_rejects_invalid_or_oversized_images(client, db):
    _login(client, db, 2)

    oversized = client.post(
        "/settings/avatar",
        data={"action": "upload", "avatar": (_image_file((513, 10)), "wide.png")},
        content_type="multipart/form-data",
        headers={"Accept": "text/html"},
    )
    assert oversized.status_code == 400

    invalid = client.post(
        "/settings/avatar",
        data={"action": "upload", "avatar": (BytesIO(b"not an image"), "avatar.png")},
        content_type="multipart/form-data",
        headers={"Accept": "text/html"},
    )
    assert invalid.status_code == 400

    with db.cursor() as cursor:
        cursor.execute("SELECT 1 FROM account_avatar WHERE account_id = 2")
        assert cursor.fetchone() is None
