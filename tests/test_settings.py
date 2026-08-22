from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb


def test_settings_persist_by_scope_and_preserve_unrelated_values(client, db, login):
    original = db.execute("SELECT settings FROM account WHERE id = 2").fetchone()["settings"]
    session_id = login(2)

    @settings(max_examples=18, deadline=None)
    @given(
        theme=st.sampled_from(["auto", "light", "dark"]),
        hide_avatars=st.booleans(),
        authenticated=st.booleans(),
    )
    def property_test(theme, hide_avatars, authenticated):
        try:
            db.execute(
                "UPDATE account SET settings = %s WHERE id = 2",
                (Jsonb({"preserved": {"value": 1}}),),
            )
            db.commit()
            if authenticated:
                client.set_cookie("session", session_id)
            else:
                client.delete_cookie("session")

            form = {"theme": theme}
            if hide_avatars:
                form["hide_message_avatars"] = "true"
            response = client.post("/settings", data=form, headers={"Accept": "text/html"})

            assert response.status_code == 200
            cookie = client.get_cookie("preferences")
            assert cookie is not None
            assert cookie.value == f"theme={theme}"
            stored = db.execute("SELECT settings FROM account WHERE id = 2").fetchone()["settings"]
            if authenticated:
                assert stored == {
                    "preserved": {"value": 1},
                    "theme": theme,
                    "messages": {"hide_avatars": hide_avatars},
                }
            else:
                assert stored == {"preserved": {"value": 1}}
        finally:
            client.delete_cookie("session")
            client.delete_cookie("preferences")

    try:
        property_test()
    finally:
        client.delete_cookie("session")
        client.delete_cookie("preferences")
        db.execute("UPDATE account SET settings = %s WHERE id = 2", (Jsonb(original),))
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()
