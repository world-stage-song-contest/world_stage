import csv
import io

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.routes.admin.recap import get_cytube_playlist
from world_stage.utils.show_metadata import validate_metadata

MEDIA = "https://media.world-stage.org"
media_urls = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1).map(
    lambda path: f"{MEDIA}/{path}.json"
)


def test_metadata_controls_playlist(app, client, db, login):
    login(1)
    db.execute("INSERT INTO show (year_id, show_type) VALUES (2025, 'f')")
    db.commit()

    @settings(max_examples=30, deadline=None)
    @given(
        opening=st.booleans(), countdown=st.booleans(),
        act=media_urls, intervals=st.lists(media_urls, max_size=20),
    )
    def check(opening, countdown, act, intervals):
        for path, metadata in (
            ("/admin/manage/2025", {"opening": opening, "countdown": countdown}),
            ("/admin/manage/2025/f", {"opening": act, "intervals": intervals}),
        ):
            response = client.post(path, json={"action": "set_metadata", "metadata": metadata})
            assert response.status_code == 200
        with app.app_context():
            playlist = get_cytube_playlist(["2025-f"])
        rows = list(csv.reader(io.StringIO(playlist), delimiter=";"))
        assert rows[0][1] == (
            f"{MEDIA}/openings/2025.mov" if opening else f"{MEDIA}/openings/ws_opening.json"
        )
        assert rows[1] == ["Opening act", act]
        start = rows.index(["Recap 1", f"{MEDIA}/recaps/2025f.mov"])
        middle = rows.index(["Recap 2", f"{MEDIA}/recaps/2025fs.mov"])
        countdown_url = (
            f"{MEDIA}/countdown/2025.mov" if countdown
            else f"{MEDIA}/countdown/countdown_with_sound.json"
        )
        end = rows.index(["Countdown", countdown_url])
        chunks = [rows[start + 1:middle], rows[middle + 1:end], rows[end + 1:]]
        assert [row[1] for chunk in chunks for row in chunk] == intervals
        lengths = [len(chunk) for chunk in chunks]
        assert lengths == sorted(lengths, reverse=True)
        assert max(lengths) - min(lengths) <= 1

    try:
        check()
    finally:
        db.execute("UPDATE year SET metadata = '{}' WHERE id = 2025")
        db.commit()


def test_opening_placement_follows_actual_shows(app, db):
    song_id = db.execute(
        "INSERT INTO song (year_id, country_id) VALUES (2024, 'US') RETURNING id"
    ).fetchone()["id"]
    db.commit()

    @settings(max_examples=16, deadline=None)
    @given(semis=st.integers(min_value=1, max_value=7), repechage=st.booleans())
    def check(semis, repechage):
        db.execute("DELETE FROM show WHERE year_id = 2025")
        db.execute("DELETE FROM country_year_results WHERE year_id = 2024")
        for number in range(1, semis + 1):
            db.execute(
                "INSERT INTO show (year_id, show_type, show_number) VALUES (2025, 'sf', %s)",
                (number,),
            )
        db.execute("INSERT INTO show (year_id, show_type) VALUES (2025, 'f')")
        if repechage:
            db.execute("INSERT INTO show (year_id, show_type) VALUES (2025, 'sc')")
        names = ["f"] + (["sc"] if repechage else [])
        names += [f"sf{number}" for number in range(semis, 0, -1)]
        for place, name in enumerate(names, start=1):
            db.execute("DELETE FROM country_year_results WHERE year_id = 2024")
            db.execute(
                """INSERT INTO country_year_results
                   (country_id, country_name, year_id, song_id, place, total_countries,
                    placement_percentage)
                   VALUES ('US', 'United States', 2024, %s, %s, 10, 50)""",
                (song_id, place),
            )
            db.commit()
            with app.app_context():
                playlist = get_cytube_playlist([f"2025-{name}"])
            rows = list(csv.reader(io.StringIO(playlist), delimiter=";"))
            assert ["Opening act", f"{MEDIA}/ws2024us.json"] in rows

    check()


@given(url=media_urls, prefix=st.sampled_from([
    "https://media.world-stage.org.evil.example/", "https://evil.example/",
    "https://media.world-stage.org@evil.example/", "javascript:",
]))
def test_metadata_rejects_foreign_media_urls(url, prefix):
    assert validate_metadata({"opening": url, "intervals": [url]}) is None
    assert validate_metadata({"opening": prefix + url}) is not None
    assert validate_metadata({"intervals": [url, prefix + url]}) is not None


def test_special_openings_do_not_use_previous_special_results(db, isolated_example):
    from psycopg.types.json import Jsonb

    from world_stage.utils.show_metadata import get_show_segments

    @settings(max_examples=12, deadline=None)
    @given(year=st.integers(-300, -100), opening=st.one_of(st.none(), media_urls))
    def check(year, opening):
        with isolated_example():
            for year_id in (year - 1, year):
                db.execute(
                    """INSERT INTO year (id, special_name, special_short_name)
                       VALUES (%s, 'Opening Test', %s)""",
                    (year_id, f"opening-test-{abs(year_id)}"),
                )
            song_id = db.execute(
                "INSERT INTO song (year_id, country_id) VALUES (%s, 'US') RETURNING id",
                (year - 1,),
            ).fetchone()["id"]
            db.execute(
                """INSERT INTO country_year_results
                   (country_id, country_name, year_id, song_id, place, total_countries,
                    placement_percentage)
                   VALUES ('US', 'United States', %s, %s, 1, 1, 100)""",
                (year - 1, song_id),
            )
            show_id = db.execute(
                """INSERT INTO show (year_id, show_type, metadata)
                   VALUES (%s, 'f', %s) RETURNING id""",
                (year, Jsonb({"opening": opening})),
            ).fetchone()["id"]
            intro, _ = get_show_segments(db.cursor(), show_id)
            assert [entry["url"] for entry in intro[1:]] == ([opening] if opening else [])

    check()
