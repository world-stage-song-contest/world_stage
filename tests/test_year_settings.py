from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb


def test_year_form_saves_all_settings_together(client, db, login):
    login(1)
    db.execute(
        """INSERT INTO year (id, status, special_name, special_short_name)
           VALUES (-2033, 'open', 'Settings Special', 'settings-special') ON CONFLICT DO NOTHING"""
    )
    original = db.execute("SELECT * FROM year WHERE id = 2025").fetchone()
    db.commit()

    @settings(max_examples=24, deadline=None)
    @given(
        special=st.booleans(), status=st.sampled_from(["open", "closed", "ongoing"]),
        style=st.sampled_from(["", "esc-1997"]), opening=st.booleans(),
        countdown=st.booleans(), submissions=st.booleans(), clear_host=st.booleans(),
    )
    def check(special, status, style, opening, countdown, submissions, clear_host):
        year = -2033 if special else 2025
        db.execute(
            """UPDATE year SET status = 'open', host_id = %s,
                      metadata = '{"other": "keep"}' WHERE id = %s""",
            (None if special else 'US', year),
        )
        db.commit()
        form = {"year_status": status, "scoreboard_style": style}
        if not special:
            form["host_id"] = "" if clear_host else "US"
        for name, value in (("opening", opening), ("countdown", countdown),
                            ("submissions_open", submissions)):
            if value:
                form[name] = "on"
        response = client.post(f"/admin/manage/{year}", data=form)
        assert response.status_code == 302
        assert response.location == (
            "/admin/manage/special/settings-special" if special else "/admin/manage/2025"
        )
        row = db.execute(
            """SELECT status, scoreboard_style, submissions_open, metadata, host_id
               FROM year WHERE id = %s""", (year,)
        ).fetchone()
        assert row == {
            "status": status, "scoreboard_style": style or None,
            "submissions_open": submissions,
            "metadata": {"other": "keep", "opening": opening, "countdown": countdown},
            "host_id": None if special or clear_host else "US",
        }
        assert client.get(response.location, headers={"Accept": "text/html"}).status_code == 200

    try:
        check()
    finally:
        db.execute(
            """UPDATE year SET status = %s, host_id = %s, submissions_open = %s,
                      scoreboard_style = %s, metadata = %s WHERE id = 2025""",
            (original["status"], original["host_id"], original["submissions_open"],
             original["scoreboard_style"], Jsonb(original["metadata"])),
        )
        db.commit()


def test_failed_year_save_rolls_back_host_assignment(client, db, login):
    login(1)
    db.execute("INSERT INTO show (year_id, show_type) VALUES (2025, 'f')")
    for country in ('ES', 'FR'):
        song = db.execute(
            "INSERT INTO song (year_id, country_id) VALUES (2025, %s) RETURNING id", (country,)
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO song_data (song_id, title, artist_credit_set_id)
               VALUES (%s, 'Song', test_artist_credit('Artist'))""", (song,)
        )
    db.commit()

    @settings(max_examples=12, deadline=None)
    @given(host=st.sampled_from(['ES', 'FR']), opening=st.booleans(), countdown=st.booleans())
    def check(host, opening, countdown):
        before = db.execute("SELECT * FROM year WHERE id = 2025").fetchone()
        form = {"year_status": "ongoing", "host_id": host, "scoreboard_style": ""}
        if opening:
            form['opening'] = 'on'
        if countdown:
            form['countdown'] = 'on'
        response = client.post('/admin/manage/2025', data=form)
        assert response.status_code == 400
        assert db.execute("SELECT * FROM year WHERE id = 2025").fetchone() == before
        assert db.execute("SELECT COUNT(*) AS n FROM song_show").fetchone()['n'] == 0

    check()
