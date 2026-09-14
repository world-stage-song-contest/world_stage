import json
import subprocess
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb

MEDIA = "https://media.world-stage.org"


def test_full_show_is_optional_and_preserves_song_order(client, db, login):
    login(1)
    show_id = db.execute(
        "INSERT INTO show (year_id, show_type) VALUES (2025, 'f') RETURNING id"
    ).fetchone()["id"]
    songs = []
    for position, country in enumerate(("US", "ES", "FR"), 1):
        song_id = db.execute(
            "INSERT INTO song (year_id, country_id) VALUES (2025, %s) RETURNING id", (country,)
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO song_data (song_id, artist_credit_set_id, title, video_link)
               VALUES (%s, test_artist_credit('Artist'), 'Song', %s)""",
            (song_id, f"{MEDIA}/{country}.mp4"),
        )
        db.execute(
            "INSERT INTO song_show (show_id, song_id, running_order) VALUES (%s, %s, %s)",
            (show_id, song_id, position),
        )
        songs.append(song_id)
    db.commit()

    @settings(max_examples=20, deadline=None)
    @given(postcards=st.booleans(), intervals=st.integers(0, 12), special=st.booleans())
    def check(postcards, intervals, special):
        year = -2025 if special else 2025
        if special:
            db.execute(
                """INSERT INTO year (id, status, special_name, special_short_name)
                   VALUES (-2025, 'closed', 'Test Special', 'test-full-show')
                   ON CONFLICT DO NOTHING"""
            )
        urls = [f"{MEDIA}/act-{i}.json" for i in range(intervals)]
        db.execute(
            "UPDATE show SET year_id = %s, metadata = %s WHERE id = %s",
            (year, Jsonb({"opening": f"{MEDIA}/opening.mov", "intervals": urls}), show_id),
        )
        db.commit()
        path = "/year/special/test-full-show/f/play" if special else "/year/2025/f/play"
        def get(**query):
            response = client.get(
                path, query_string={"postcards": str(postcards).lower(), **query},
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 200
            return response.get_json()["entries"]

        normal = get()
        full = get(full_show="true")
        assert get(full_show="false") == normal
        assert [entry["id"] for entry in full if entry["kind"] == "song"] == songs
        assert [entry for entry in full if entry["kind"] in ("song", "postcard")] == normal[:-1]
        assert [entry["kind"] for entry in normal] == (
            [kind for _ in songs for kind in (["postcard", "song"] if postcards else ["song"])]
            + ["recap"]
        )
        assert [entry["url"] for entry in full if entry["kind"] == "interval"] == urls
        assert full[0]["kind"] == "opening"
        assert full[1]["url"] == f"{MEDIA}/opening.mov"
        fixed = [entry for entry in full if entry["kind"] not in ("song", "postcard")]
        assert all(entry["shuffleable"] is False for entry in fixed)
        assert [entry["kind"] for entry in fixed if entry["kind"] != "interval"] == [
            "opening", "opening", "announcement", "recap", "recap", "countdown"
        ]

    check()


@settings(max_examples=20, deadline=None)
@given(
    stem=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=20),
    content_type=st.sampled_from(["audio/mp4", "video/mp4", "video/webm"]),
    manifest=st.booleans(),
)
def test_player_resolves_media_manifests(stem, content_type, manifest):
    source = f"{MEDIA}/{stem}.mov"
    url = f"{MEDIA}/{stem}.json" if manifest else source
    script = """
        global.window = {location: {href: 'https://world-stage.org/2025/f/play'}};
        require('./world_stage/static/js/media-source.js');
        let input = '';
        for await (const chunk of process.stdin) input += chunk;
        const data = JSON.parse(input);
        let calls = 0;
        global.fetch = async () => {
            calls++;
            return {ok: true, url: data.url, json: async () => ({sources: [
                {url: data.source, contentType: data.contentType}
            ]})};
        };
        const result = await window.WorldStageMediaSource.resolve(data.url);
        console.log(JSON.stringify({result, calls}));
    """
    result = subprocess.run(
        ["node", "-e", f"(async () => {{{script}}})()"],
        input=json.dumps({"url": url, "source": source, "contentType": content_type}),
        text=True, capture_output=True, check=True, cwd=Path(__file__).resolve().parents[1],
    )
    data = json.loads(result.stdout)
    assert data["result"]["url"] == source
    assert data["calls"] == int(manifest)
    if manifest:
        assert data["result"]["contentType"] == content_type
