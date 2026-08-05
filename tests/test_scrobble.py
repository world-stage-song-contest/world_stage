"""Tests for radio scrobbling (Last.fm / Libre.fm)."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from world_stage import scrobble
from world_stage.routes import radio


def _add_song(db, cc, year, title, artist, link, duration):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO song (country_id, year_id, entry_number)
            VALUES (%s, %s, 1) RETURNING id
            """,
            (cc, year),
        )
        song_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist, video_link,
                   duration
               ) VALUES (%s, 1, %s, %s, %s, %s)""",
            (song_id, title, artist, link, duration),
        )
    db.commit()
    return song_id


def _make_session(db, user_id):
    sid = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO session (user_id, session_id, expires_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')
            """,
            (user_id, sid),
        )
    db.commit()
    return sid


@pytest.fixture(autouse=True)
def _clean_scrobble(_seeded_db):
    """Drop scrobble accounts and sessions created during a test."""
    yield
    import psycopg

    conn = psycopg.connect(_seeded_db)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM scrobble_account")
        cur.execute("DELETE FROM session")
    conn.commit()
    conn.close()


# ── _sign (pure, no app needed) ──────────────────────────────────────


def test_sign_excludes_format_and_appends_secret():
    params = {"api_key": "k", "method": "auth.getSession", "token": "t", "format": "json"}
    expected = hashlib.md5(
        ("api_keykmethodauth.getSessiontokent" + "s").encode("utf-8")
    ).hexdigest()
    assert scrobble._sign(params, "s") == expected


# ── _validate_submission ─────────────────────────────────────────────


class TestValidateSubmission:
    def test_valid_submission_returns_server_slot(self, app, client, db):
        _add_song(db, "US", 2024, "Real Title", "Real Artist", "https://m/x.mp4", 200.0)
        slot = client.get("/radio/now").get_json()
        with app.app_context():
            got = radio._validate_submission(slot["slot_id"])
        assert got is not None
        assert got["song"]["title"] == "Real Title"

    def test_future_slot_rejected(self, app, client, db):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        client.get("/radio/now")
        client.get("/radio/now")
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM radio_slot
                WHERE starts_at > clock_timestamp()
                ORDER BY starts_at
                LIMIT 1
                """
            )
            future = cursor.fetchone()
        assert future is not None
        with app.app_context():
            assert radio._validate_submission(future["id"]) is None

    def test_stale_slot_rejected(self, app, db):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        old = datetime.now(UTC) - timedelta(seconds=radio.SCROBBLE_MAX_AGE + 60)
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM get_current_radio_slot(%s)", (old,))
            slot = cursor.fetchone()
        db.commit()
        assert slot is not None
        with app.app_context():
            assert radio._validate_submission(slot["slot_id"]) is None

    def test_unknown_slot_id_rejected(self, app, client, db):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        slot = client.get("/radio/now").get_json()
        with app.app_context():
            assert radio._validate_submission(slot["slot_id"] + 9999) is None


# ── POST endpoints ───────────────────────────────────────────────────


class TestScrobbleEndpoints:
    @pytest.fixture
    def fake_call(self, monkeypatch):
        calls = []

        def _fake(service, method, params, *, post):
            calls.append({"service": service, "method": method, "params": params, "post": post})
            if method == "auth.getSession":
                return {"session": {"key": "KEY-" + service, "name": "remote_" + service}}
            return {"ok": 1}

        monkeypatch.setattr(scrobble, "_call", _fake)
        monkeypatch.setattr(scrobble, "is_configured", lambda s: True)
        return calls

    def _link(self, db, user_id, service="lastfm"):
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrobble_account (
                    user_id, service, session_key, remote_username, enabled
                )
                VALUES (%s, %s, 'SK', 'remote', true)
                """,
                (user_id, service),
            )
        db.commit()

    def test_anonymous_scrobble_is_noop(self, client, db, fake_call):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        resp = client.post("/radio/scrobble", json={"slot_id": 1})
        assert resp.status_code == 204
        assert fake_call == []

    def test_scrobble_uses_scheduled_snapshot_not_request_or_catalog(
        self, client, db, fake_call
    ):
        _add_song(db, "US", 2024, "Server Title", "Server Artist", "https://m/x.mp4", 200.0)
        self._link(db, 2)  # bob
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        slot = client.get("/radio/now").get_json()
        with db.cursor() as cur:
            from world_stage.utils.song_revisions import create_song_revision
            create_song_revision(
                cur, slot["song"]["id"],
                {"title": "Changed Title", "artist": "Changed Artist"},
                changed_by=None,
            )
        db.commit()

        resp = client.post(
            "/radio/scrobble",
            json={
                "slot_id": slot["slot_id"],
                "artist": "HACKED",  # must be ignored
                "track": "HACKED",
            },
        )
        assert resp.status_code == 204

        submitted = [c for c in fake_call if c["method"] == "track.scrobble"]
        assert len(submitted) == 1
        params = submitted[0]["params"]
        assert params["artist"] == "Server Artist"
        assert params["track"] == "Server Title"
        assert params["timestamp"] == int(slot["slot_start"])

        with db.cursor() as cur:
            cur.execute("SELECT last_scrobbled_at FROM scrobble_account WHERE user_id = 2")
            assert cur.fetchone()["last_scrobbled_at"] is not None

    def test_invalid_slot_id_is_ignored(self, client, db, fake_call):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        self._link(db, 2)
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)
        client.get("/radio/now")

        resp = client.post(
            "/radio/scrobble",
            json={"slot_id": "invalid"},
        )

        assert resp.status_code == 204
        assert [call for call in fake_call if call["method"] == "track.scrobble"] == []

    def test_now_playing_sends_update(self, client, db, fake_call):
        _add_song(db, "US", 2024, "NP Title", "NP Artist", "https://m/x.mp4", 200.0)
        self._link(db, 2)
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        slot = client.get("/radio/now").get_json()

        resp = client.post(
            "/radio/now-playing",
            json={"slot_id": slot["slot_id"]},
        )
        assert resp.status_code == 204
        np = [c for c in fake_call if c["method"] == "track.updateNowPlaying"]
        assert len(np) == 1
        assert "timestamp" not in np[0]["params"]

    def test_scrobble_through_real_creds_and_threads(self, client, db, monkeypatch):
        # Exercise the genuine path — real _creds()/_sign() running inside
        # the dispatch worker threads — rather than stubbing _call(). This
        # is the configuration that surfaced the worker-thread app-context
        # bug; only the network leaf (urlopen) is faked.
        monkeypatch.setenv("LASTFM_API_KEY", "k")
        monkeypatch.setenv("LASTFM_API_SECRET", "s")

        seen = []

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"scrobbles": {"@attr": {"accepted": 1}}}'

        def fake_urlopen(req, timeout):
            seen.append(getattr(req, "full_url", None) or req.get_full_url())
            return FakeResp()

        monkeypatch.setattr(scrobble.urllib.request, "urlopen", fake_urlopen)

        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        self._link(db, 2)
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)
        slot = client.get("/radio/now").get_json()

        resp = client.post(
            "/radio/scrobble",
            json={"slot_id": slot["slot_id"]},
        )
        assert resp.status_code == 204  # not 500 — worker thread had app context
        assert any("audioscrobbler" in u for u in seen)

    def test_disabled_account_does_not_scrobble(self, client, db, fake_call):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrobble_account (user_id, service, session_key, enabled)
                VALUES (2, 'lastfm', 'SK', false)
                """
            )
        db.commit()
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)
        slot = client.get("/radio/now").get_json()
        client.post(
            "/radio/scrobble",
            json={"slot_id": slot["slot_id"]},
        )
        assert [c for c in fake_call if c["method"] == "track.scrobble"] == []

    def test_catalog_playback_endpoints_use_authoritative_song(self, client, db, fake_call):
        song_id = _add_song(
            db, "US", 2024, "Catalog Title", "Catalog Artist", "https://m/x.mp4", 180.0
        )
        self._link(db, 2)
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        now_response = client.post(
            "/scrobble/now-playing",
            json={"song_id": song_id, "artist": "HACKED", "track": "HACKED"},
        )
        timestamp = int(datetime.now(UTC).timestamp())
        scrobble_response = client.post(
            "/scrobble",
            json={
                "song_id": song_id,
                "timestamp": timestamp,
                "artist": "HACKED",
                "track": "HACKED",
            },
        )

        assert now_response.status_code == 204
        assert scrobble_response.status_code == 204
        submitted = [
            call for call in fake_call
            if call["method"] in ("track.updateNowPlaying", "track.scrobble")
        ]
        assert [call["params"]["artist"] for call in submitted] == [
            "Catalog Artist", "Catalog Artist"
        ]
        assert [call["params"]["track"] for call in submitted] == [
            "Catalog Title", "Catalog Title"
        ]
        assert submitted[1]["params"]["timestamp"] == timestamp
        assert submitted[1]["params"]["album"] is None

    def test_catalog_scrobble_rejects_invalid_timestamp(self, client, db, fake_call):
        song_id = _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 180.0)
        self._link(db, 2)
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        response = client.post(
            "/scrobble",
            json={"song_id": song_id, "timestamp": "not-a-timestamp"},
        )

        assert response.status_code == 204
        assert [call for call in fake_call if call["method"] == "track.scrobble"] == []


# ── Connect / callback ───────────────────────────────────────────────


class TestConnectFlow:
    @pytest.fixture
    def fake_call(self, monkeypatch):
        def _fake(service, method, params, *, post):
            if method == "auth.getSession":
                return {"session": {"key": "SK-" + service, "name": "lfm_user"}}
            return {"ok": 1}

        monkeypatch.setattr(scrobble, "_call", _fake)
        monkeypatch.setattr(scrobble, "is_configured", lambda s: True)

    def test_callback_upserts_once_and_is_idempotent(self, client, db, fake_call):
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        for _ in range(2):
            resp = client.get("/settings/scrobble/lastfm/callback?token=abc")
            assert resp.status_code in (301, 302)

        with db.cursor() as cur:
            cur.execute(
                "SELECT session_key, remote_username "
                "FROM scrobble_account WHERE user_id = 2"
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["session_key"] == "SK-lastfm"
        assert rows[0]["remote_username"] == "lfm_user"


def test_site_scrobble_client_uses_lastfm_threshold_and_catalog_id(client):
    response = client.get("/static/js/scrobble.js")
    assert response.status_code == 200
    source = response.get_data(as_text=True)

    assert "Math.min(duration / 2, 240)" in source
    assert "song_id: this.song.id" in source
    assert "post('/scrobble/now-playing'" in source
    assert "post('/scrobble'" in source


def test_show_player_tracks_only_song_entries():
    source = Path("world_stage/templates/year/play.html").read_text(encoding="utf-8")
    assert "entry.kind === 'song' ? entry : null" in source
    assert "scrobbleTracker.setSong" in source
