import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage import listens


def _save_play(app, user_id, snapshot, played_at):
    with app.app_context():
        return listens.record_play(user_id, snapshot, played_at)


def test_listens_keep_playback_snapshots_after_edits_and_deletion(app, client, db, login):
    login()
    now = int(datetime.now(UTC).timestamp())

    @settings(max_examples=12, deadline=None)
    @given(
        title=st.text(alphabet="abcdefghijké日本", min_size=1, max_size=20),
        radio=st.booleans(),
        delete_before_finish=st.booleans(),
    )
    def check(title, radio, delete_before_finish):
        db.execute("DELETE FROM song_play")
        db.execute("DELETE FROM radio_slot")
        song_id = db.execute(
            "INSERT INTO song (country_id, year_id) VALUES ('US', 2024) RETURNING id"
        ).fetchone()["id"]
        revision_ids = []

        def revise(name, artist, media):
            revision_ids.append(db.execute(
                """INSERT INTO song_data (
                       song_id, title, artist_credit_set_id, video_link, duration
                   ) VALUES (%s, %s, test_artist_credit(%s), %s, 200) RETURNING id""",
                (song_id, name, artist, media),
            ).fetchone()["id"])
            db.commit()

        original_media = "https://media.world-stage.org/original.mp4"
        revise(title, "Original artist", original_media)
        with app.app_context():
            catalog_body = {"play_snapshot": listens.song_snapshot_token(song_id)}
        if radio:
            slot = client.get("/radio/now").get_json()
            original_body = {"radio_slot_id": slot["slot_id"]}
        else:
            page = client.get("/country/us/2024", headers={"Accept": "application/json"})
            assert page.status_code == 200
            original_body = {"play_snapshot": page.get_json()["play_snapshot"]}

        def submit(body, timestamp):
            response = client.post("/scrobble/play", json={
                **body, "song_id": song_id, "timestamp": timestamp,
                "heard_seconds": 100, "duration": 200,
                "title": "Untrusted title", "artist": "Untrusted artist",
            })
            assert response.status_code == 204

        submit(original_body, now - 180)
        db.execute("UPDATE song SET country_id = 'ES', year_id = 2025 WHERE id = %s", (song_id,))
        revise(title + " replacement", "New artist", "https://media.world-stage.org/new.mp4")
        with app.app_context():
            replacement_body = {"play_snapshot": listens.song_snapshot_token(song_id)}

        def delete_song():
            db.execute("DELETE FROM song_data WHERE id = ANY(%s)", (revision_ids,))
            db.execute("DELETE FROM song WHERE id = %s", (song_id,))
            db.commit()

        if delete_before_finish:
            delete_song()
        submit(original_body, now - 120)
        submit(catalog_body, now - 90)
        submit(replacement_body, now - 60)
        if not delete_before_finish:
            delete_song()

        history = client.get(
            "/user/bob/listen-history", headers={"Accept": "application/json"}
        ).get_json()["plays"]
        assert len(history) == 4
        assert [play["title"] for play in history] == [title + " replacement", title, title, title]
        for play in history[1:]:
            assert play["artist"] == "Original artist"
            assert play["country"] == "United States"
            assert play["year_label"] == "2024"
            assert play["song_url"] == original_media
        assert history[0]["country"] == "Spain"
        assert history[0]["year_label"] == "2025"
        summary = client.get(
            "/user/bob/listen-history/summary", headers={"Accept": "application/json"}
        ).get_json()["songs"]
        assert [(song["title"], song["listen_count"]) for song in summary] == [
            (title, 3), (title + " replacement", 1)
        ]

    check()


def test_public_listen_history_preserves_each_users_plays_in_date_order(app, client, db, login):
    db.execute(
        """INSERT INTO year (id, status, special_name, special_short_name)
           VALUES (-2024, 'closed', 'History Special', 'history-special')"""
    )
    songs = []
    for year, entry_number in [(2024, 2), (-2024, 1), (2024, 3)]:
        song_id = db.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               VALUES ('US', %s, %s) RETURNING id""",
            (year, entry_number),
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO song_data (song_id, title, artist_credit_set_id)
               VALUES (%s, 'History song', test_artist_credit('History artist'))""",
            (song_id,),
        )
        songs.append(song_id)
    national_final_id = db.execute(
        """INSERT INTO national_final (year_id, owner_id, short_name, name)
           VALUES (2024, 2, 'history-final', 'History Final') RETURNING id"""
    ).fetchone()["id"]
    db.execute("UPDATE song SET main_participant = false WHERE id = %s", (songs[2],))
    db.execute(
        "INSERT INTO national_final_song (national_final_id, song_id) VALUES (%s, %s)",
        (national_final_id, songs[2]),
    )
    db.commit()
    now = int(datetime.now(UTC).timestamp())

    @settings(max_examples=15, deadline=None)
    @given(
        events=st.one_of(
            st.integers(0, 110).flatmap(
                lambda size: st.lists(
                    st.tuples(st.integers(0, 2), st.integers(0, 10)), min_size=size, max_size=size
                )
            ),
            st.lists(st.integers(0, 10), min_size=3, max_size=3).map(
                lambda ages: list(enumerate(ages))
            ),
        ),
        target=st.sampled_from([(2, "bob"), (3, "carol")]),
        viewer=st.sampled_from([None, 2, 3]),
        page_input=st.one_of(st.integers(-10, 100), st.just("invalid")),
        national_final_name=st.text(
            alphabet="Abcdefghijklmnopqrstuvwxyzé日本", min_size=1, max_size=30
        ),
    )
    def check(events, target, viewer, page_input, national_final_name):
        db.execute("DELETE FROM song_play")
        db.execute(
            "UPDATE national_final SET name = %s WHERE id = %s",
            (national_final_name, national_final_id),
        )
        db.commit()
        with app.app_context():
            snapshots = listens.catalog_snapshots(songs)
        year_labels = dict(zip(
            songs, ["2024", "History Special", national_final_name], strict=True
        ))
        expected = {}
        for song_index, age in events:
            song_id = songs[song_index]
            played_at = now - age
            play_id = _save_play(app, target[0], snapshots[song_id], played_at)
            expected[play_id] = (song_id, played_at)
        _save_play(app, 5 - target[0], snapshots[songs[0]], now + 1)
        db.commit()
        if viewer is None:
            client.delete_cookie("session")
        else:
            login(viewer)

        history_url = f"/user/{target[1].upper()}/listen-history"
        url = history_url
        seen = []
        song_urls = set()
        for _ in range(len(events) + 1):
            response = client.get(url, headers={"Accept": "application/json"})
            assert response.status_code == 200
            data = response.get_json()
            assert data["username"] == target[1]
            assert 1 <= data["page"] <= data["pages"]
            for play in data["plays"]:
                played_at = int(parsedate_to_datetime(play["played_at"]).timestamp())
                assert expected[play["id"]] == (play["song_id"], played_at)
                assert play["title"] == "History song"
                assert play["artist"] == "History artist"
                assert play["year_label"] == year_labels[play["song_id"]]
                seen.append((play["id"], played_at))
                song_urls.add(play["song_url"])
            if data["next_url"] is None:
                break
            url = data["next_url"]
        else:
            raise AssertionError("Listen history pagination did not end")

        assert len(seen) == len(expected)
        assert {play_id for play_id, _time in seen} == set(expected)
        times = [time for _play_id, time in seen]
        assert times == sorted(times, reverse=True)
        for song_url in song_urls:
            assert client.get(song_url, headers={"Accept": "application/json"}).status_code == 200
        response = client.get(
            history_url, query_string={"page": page_input}, headers={"Accept": "application/json"}
        )
        data = response.get_json()
        assert response.status_code == 200
        assert 1 <= data["page"] <= data["pages"]
        assert client.get(history_url, headers={"Accept": "text/html"}).status_code == 200
        assert client.get(f"/user/missing-{target[1]}/listen-history").status_code == 404

        summary_url = f"{history_url}/summary"
        response = client.get(summary_url, headers={"Accept": "application/json"})
        assert response.status_code == 200
        summary = response.get_json()
        assert summary["username"] == target[1]
        counts = Counter(song_id for song_id, _time in expected.values())
        assert len(summary["songs"]) == len(counts)
        assert {song["song_id"]: song["listen_count"] for song in summary["songs"]} == counts
        latest = {}
        for song_id, played_at in expected.values():
            latest[song_id] = max(latest.get(song_id, played_at), played_at)
        ordering = [(song["listen_count"], latest[song["song_id"]]) for song in summary["songs"]]
        assert ordering == sorted(ordering, reverse=True)
        assert {song["song_url"] for song in summary["songs"]} == song_urls
        assert all(song["year_label"] == year_labels[song["song_id"]] for song in summary["songs"])
        assert client.get(summary_url, headers={"Accept": "text/html"}).status_code == 200
        assert client.get(f"/user/missing-{target[1]}/listen-history/summary").status_code == 404

    check()


def test_each_play_is_recorded_with_its_user_song_and_time_without_scrobbling(
    app, client, db, login, monkeypatch
):
    from world_stage import scrobble

    def unexpected_dispatch(*args, **kwargs):
        raise AssertionError("Local plays must not contact scrobbling services")

    monkeypatch.setattr(scrobble, "send_to_all", unexpected_dispatch)
    songs = []
    for country in ("US", "ES"):
        song_id = db.execute(
            "INSERT INTO song (country_id, year_id) VALUES (%s, 2024) RETURNING id",
            (country,),
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO song_data (song_id, title, artist_credit_set_id, duration)
               VALUES (%s, 'Played song', test_artist_credit('Artist'), 200)""",
            (song_id,),
        )
        songs.append(song_id)
    db.execute("DELETE FROM scrobble_account")
    db.commit()
    with app.app_context():
        tokens = {song_id: listens.song_snapshot_token(song_id) for song_id in songs}
    now = int(datetime.now(UTC).timestamp())

    @settings(max_examples=30, deadline=None)
    @given(
        events=st.lists(
            st.tuples(
                st.sampled_from([None, 2, 3]),
                st.integers(0, 2),
                st.one_of(st.integers(0, 500), st.just(15.5), st.none(), st.booleans()),
                st.one_of(
                    st.integers(now - 1000, now - 120),
                    st.sampled_from([None, True, "invalid", now - 100000, now + 100000]),
                ),
                st.one_of(st.integers(1, 1000), st.none(), st.booleans(), st.just("invalid")),
                st.booleans(),
            ),
            min_size=1,
            max_size=15,
        )
    )
    def check(events):
        db.execute("DELETE FROM song_play")
        db.commit()
        expected = []
        for user_id, song_index, heard, timestamp, duration, use_catalog in events:
            if user_id is None:
                client.delete_cookie("session")
            else:
                login(user_id)
            song_id = songs[song_index] if song_index < len(songs) else -1
            response = client.post(
                "/scrobble/play",
                json={
                    "song_id": song_id,
                    "play_snapshot": tokens.get(song_id),
                    "timestamp": timestamp,
                    "heard_seconds": heard,
                    "user_id": 1,
                    **({} if use_catalog else {"duration": duration}),
                },
            )
            assert response.status_code == 204
            effective_duration = 200 if use_catalog else duration
            if (
                user_id
                and song_id in songs
                and isinstance(heard, (int, float))
                and isinstance(effective_duration, (int, float))
                and effective_duration > 30
                and heard >= min(effective_duration / 2, 240)
                and isinstance(timestamp, int)
                and now - 1000 <= timestamp <= now - 120
            ):
                expected.append((user_id, song_id, timestamp))
        actual = [
            (
                row["user_id"],
                row["song_id"],
                int(row["played_at"].timestamp()),
            )
            for row in db.execute("SELECT user_id, song_id, played_at FROM song_play").fetchall()
        ]
        assert sorted(actual) == sorted(expected)

    check()


@settings(max_examples=30, deadline=None)
@given(
    plays=st.lists(
        st.lists(
            st.tuples(
                st.one_of(
                    st.integers(0, 300000),
                    st.sampled_from([15499, 15500, 15501, 239999, 240000, 240001]),
                ),
                st.integers(0, 60000),
            ),
            min_size=1,
            max_size=5,
        ),
        min_size=1,
        max_size=4,
    ),
    enabled=st.booleans(),
    scrobble_enabled=st.booleans(),
    duration=st.one_of(st.none(), st.integers(1, 1000), st.sampled_from([30, 31, 480, 481])),
    media_duration=st.one_of(st.none(), st.integers(1, 1000)),
    replay=st.booleans(),
)
def test_player_records_once_using_scrobble_eligibility(
    plays, enabled, scrobble_enabled, duration, media_duration, replay
):
    script = """
        const fs = require('node:fs');
        const data = JSON.parse(fs.readFileSync(0, 'utf8'));
        let now = 1800000000000;
        let nextTimer = 0;
        const timers = new Map();
        const sent = [];
        global.Date.now = () => now;
        global.setTimeout = (fn, delay) => {
            const id = ++nextTimer;
            timers.set(id, {fn, at: now + delay});
            return id;
        };
        global.clearTimeout = id => timers.delete(id);
        global.document = {addEventListener() {}};
        global.window = {addEventListener() {}};
        global.fetch = async (path, options) => {
            sent.push({path, ...JSON.parse(options.body)});
        };
        function advance(ms) {
            now += ms;
            for (const [id, timer] of [...timers]) {
                if (timer.at <= now) {
                    timers.delete(id);
                    timer.fn();
                }
            }
        }
        require('./world_stage/static/js/scrobble.js');
        const tracker = new window.WorldStageScrobble.Tracker(data.scrobble_enabled, data.enabled);
        const playCount = () => sent.filter(event => event.path === '/scrobble/play').length;
        const checkpoints = [];
        for (const [index, segments] of data.plays.entries()) {
            if (!data.replay || index === 0) {
                tracker.setSong(
                    {id: 10, duration: data.duration, play_snapshot: 'snapshot'},
                    () => data.media_duration
                );
            }
            for (const [playing, paused] of segments) {
                tracker.playing();
                tracker.playing();
                advance(playing);
                checkpoints.push(playCount());
                tracker.pause();
                advance(paused);
                tracker.flush();
                tracker.flush();
                checkpoints.push(playCount());
            }
            tracker.ended();
        }
        console.log(JSON.stringify({sent, checkpoints}));
    """
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(
            {
                "plays": plays,
                "enabled": enabled,
                "scrobble_enabled": scrobble_enabled,
                "duration": duration,
                "media_duration": media_duration,
                "replay": replay,
            }
        ),
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    actual = json.loads(result.stdout)
    checkpoints = []
    total = 0
    eligible_plays = 0
    effective_duration = media_duration or duration
    threshold = (
        min(effective_duration / 2, 240) * 1000
        if effective_duration and effective_duration > 30
        else float("inf")
    )
    for segments in plays:
        heard = 0
        counted = False
        for playing, _paused in segments:
            heard += playing
            if heard >= threshold and not counted:
                total += int(enabled)
                eligible_plays += 1
                counted = True
            checkpoints.extend([total, total])
    assert actual["checkpoints"] == checkpoints
    counted_plays = [event for event in actual["sent"] if event["path"] == "/scrobble/play"]
    scrobbles = [event for event in actual["sent"] if event["path"] == "/scrobble"]
    assert len(counted_plays) == total
    assert len(scrobbles) == eligible_plays * int(scrobble_enabled)
    assert all(
        event["song_id"] == 10
        and event["play_snapshot"] == "snapshot"
        and event["heard_seconds"] * 1000 >= threshold
        and event["duration"] == effective_duration
        for event in counted_plays
    )
