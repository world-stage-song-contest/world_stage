from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb

MEDIA = "https://media.world-stage.org"


def test_download_and_playback_share_show_options(client, db, login):
    login(1)
    db.execute(
        "INSERT INTO show_status (name) VALUES ('draw'), ('partial'), ('full') "
        "ON CONFLICT DO NOTHING"
    )
    show_id = db.execute(
        """INSERT INTO show (year_id, show_type, show_number, status, metadata)
           VALUES (2025, 'sf', 1, 'draw', %s) RETURNING id""",
        (Jsonb({"opening": f"{MEDIA}/opening.mov", "intervals": [f"{MEDIA}/interval.mov"]}),),
    ).fetchone()["id"]
    db.execute(
        "UPDATE year SET host_id = 'US', metadata = %s WHERE id = 2025",
        (Jsonb({"opening": True, "countdown": True}),),
    )
    for position, country in enumerate(("US", "ES", "FR")):
        song_id = db.execute(
            "INSERT INTO song (year_id, country_id) VALUES (2025, %s) RETURNING id", (country,)
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO song_data (song_id, artist_credit_set_id, title, video_link)
               VALUES (%s, test_artist_credit('Artist'), 'Title', %s)""",
            (song_id, f"{MEDIA}/{country}.mp4"),
        )
        if position:
            db.execute(
                "INSERT INTO song_show (show_id, song_id, running_order) VALUES (%s, %s, %s)",
                (show_id, song_id, position),
            )
    db.commit()

    @settings(max_examples=24, deadline=None)
    @given(
        postcards=st.booleans(), host=st.booleans(), intervals=st.booleans(),
        status=st.sampled_from(["draw", "partial", "full"]),
        suffix_order=st.permutations(["nh", "ni", "np"]),
    )
    def check(postcards, host, intervals, status, suffix_order):
        db.execute("UPDATE show SET status = %s WHERE id = %s", (status, show_id))
        db.commit()
        query = {
            name: "on" for name, enabled in
            {"postcards": postcards, "host": host, "intervals": intervals}.items()
            if enabled
        }
        response = client.get("/playlist/show/2025sf1.m3u", query_string=query)
        assert response.status_code == 200
        urls = [line for line in response.text.splitlines() if not line.startswith('#')]
        suffixes = [suffix for suffix, enabled in (
            ("nh", host), ("ni", intervals), ("np", postcards)
        ) if not enabled]
        filename = "2025sf1" + "".join(f"-{suffix}" for suffix in suffixes) + ".m3u"
        assert response.headers['Content-Disposition'].endswith(filename)
        assert (f"{MEDIA}/US.mp4" in urls) == host
        assert any('/postcards/' in url for url in urls) == postcards
        assert (f"{MEDIA}/interval.mov" in urls) == intervals
        play = client.get(
            "/year/2025/sf1/play", query_string=query, headers={"Accept": "application/json"}
        ).get_json()
        assert [entry["url"] for entry in play["entries"]] == urls
        assert play["host_available"] == (status == "full")
        default = client.get(
            "/year/2025/sf1/play", headers={"Accept": "application/json"}
        ).get_json()
        assert default["postcards"] is False
        assert default["include_host"] is False
        assert default["full_show"] is False
        legacy_key = "2025sf1" + "".join(f"-{flag}" for flag in suffix_order)
        legacy = client.get(f"/playlist/show/{legacy_key}.m3u")
        assert legacy.status_code == 200
        assert legacy.headers['Content-Disposition'].endswith('2025sf1-nh-ni-np.m3u')
        legacy_urls = [line for line in legacy.text.splitlines() if not line.startswith('#')]
        assert legacy_urls == [
            f"{MEDIA}/ES.mp4", f"{MEDIA}/FR.mp4", f"{MEDIA}/recaps/2025sf1.mov"
        ]

    try:
        check()
    finally:
        db.execute("UPDATE year SET metadata = '{}' WHERE id = 2025")
        db.commit()
