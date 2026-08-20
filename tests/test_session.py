import uuid
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

HTML_HEADERS = {"Accept": "text/html,image/svg+xml"}


@contextmanager
def _cookie_example(client):
    try:
        yield
    finally:
        client.delete_cookie("session")


def test_unknown_sessions_are_discarded_for_every_login_method(client):
    @settings(max_examples=30, deadline=None)
    @given(session_id=st.uuids(), method=st.sampled_from(["get", "post"]))
    def property_test(session_id, method):
        with _cookie_example(client):
            client.set_cookie("session", str(session_id))

            response = getattr(client, method)("/login", headers=HTML_HEADERS)

            assert response.status_code == 302
            assert response.location.endswith("/login")
            assert client.get_cookie("session") is None

    property_test()


def test_unexpired_sessions_are_preserved(client, db):
    @settings(max_examples=30, deadline=None)
    @given(
        session_id=st.uuids(version=4),
        lifetime=st.integers(min_value=1, max_value=10_000),
    )
    def property_test(session_id: uuid.UUID, lifetime: int):
        with _cookie_example(client):
            db.execute(
                """INSERT INTO session (user_id, session_id, expires_at)
                   VALUES (1, %s, CURRENT_TIMESTAMP + %s * INTERVAL '1 second')""",
                (session_id, lifetime),
            )
            db.commit()
            try:
                client.set_cookie("session", str(session_id))

                response = client.get("/login", headers=HTML_HEADERS)

                assert response.status_code == 200
                assert client.get_cookie("session").value == str(session_id)
            finally:
                db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
                db.commit()

    property_test()
