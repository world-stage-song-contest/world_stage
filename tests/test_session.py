import uuid

import pytest

HTML_HEADERS = {"Accept": "text/html,image/svg+xml"}


@pytest.mark.parametrize("method", ["get", "post"])
def test_stale_session_cookie_is_deleted_and_redirected_to_login(client, method):
    client.set_cookie("session", str(uuid.uuid4()))

    response = getattr(client, method)("/login", headers=HTML_HEADERS)

    assert response.status_code == 302
    assert response.location.endswith("/login")
    assert client.get_cookie("session") is None

    login_page = client.get(response.location, headers=HTML_HEADERS)
    assert login_page.status_code == 200


def test_valid_session_cookie_still_shows_already_logged_in(client, db):
    session_id = str(uuid.uuid4())
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (1, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')
            """,
            (session_id,),
        )
    db.commit()
    client.set_cookie("session", session_id)

    response = client.get("/login", headers=HTML_HEADERS)

    assert response.status_code == 200
    assert client.get_cookie("session").value == session_id
