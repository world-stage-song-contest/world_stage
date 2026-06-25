"""Tests for radio scrobbling (Last.fm / Libre.fm)."""

import hashlib
import time
import uuid

import pytest

from world_stage import scrobble
from world_stage.routes import radio


def _add_song(db, cc, year, title, artist, link, duration):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO song (submitter_id, country_id, year_id, title, artist,
                video_link, duration, is_placeholder, entry_number)
            VALUES (1, %s, %s, %s, %s, %s, %s, false, 1)
            """,
            (cc, year, title, artist, link, duration),
        )
    db.commit()


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
    def _live_slot(self, app):
        with app.app_context():
            return radio._song_at(time.time())

    def test_valid_submission_returns_server_slot(self, app, db):
        _add_song(db, "US", 2024, "Real Title", "Real Artist", "https://m/x.mp4", 200.0)
        with app.app_context():
            slot = radio._song_at(time.time())
            got = radio._validate_submission(slot["slot_start"], slot["song"]["id"])
        assert got is not None
        assert got["song"]["title"] == "Real Title"

    def test_future_timestamp_rejected(self, app, db):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        with app.app_context():
            slot = radio._song_at(time.time())
            assert radio._validate_submission(time.time() + 60, slot["song"]["id"]) is None

    def test_stale_timestamp_rejected(self, app, db):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        with app.app_context():
            old = time.time() - radio.SCROBBLE_MAX_AGE - 60
            assert radio._validate_submission(old, 1) is None

    def test_wrong_song_id_rejected(self, app, db):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        with app.app_context():
            slot = radio._song_at(time.time())
            assert radio._validate_submission(slot["slot_start"], slot["song"]["id"] + 9999) is None

    def test_misaligned_timestamp_rejected(self, app, db):
        # Long songs so slot_start + 30s is still inside the same window
        # but well beyond the alignment tolerance.
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 600.0)
        with app.app_context():
            slot = radio._song_at(time.time())
            off = slot["slot_start"] + 30
            assert radio._validate_submission(off, slot["song"]["id"]) is None


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
                INSERT INTO scrobble_account (user_id, service, session_key, remote_username, enabled)
                VALUES (%s, %s, 'SK', 'remote', true)
                """,
                (user_id, service),
            )
        db.commit()

    def test_anonymous_scrobble_is_noop(self, client, db, fake_call):
        _add_song(db, "US", 2024, "T", "A", "https://m/x.mp4", 200.0)
        resp = client.post("/radio/scrobble", json={"song_id": 1, "started_at": time.time()})
        assert resp.status_code == 204
        assert fake_call == []

    def test_scrobble_uses_server_metadata_not_body(self, app, client, db, fake_call):
        _add_song(db, "US", 2024, "Server Title", "Server Artist", "https://m/x.mp4", 200.0)
        self._link(db, 2)  # bob
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        with app.app_context():
            slot = radio._song_at(time.time())

        resp = client.post(
            "/radio/scrobble",
            json={
                "song_id": slot["song"]["id"],
                "started_at": slot["slot_start"],
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

    def test_now_playing_sends_update(self, app, client, db, fake_call):
        _add_song(db, "US", 2024, "NP Title", "NP Artist", "https://m/x.mp4", 200.0)
        self._link(db, 2)
        sid = _make_session(db, 2)
        client.set_cookie("session", sid)

        with app.app_context():
            slot = radio._song_at(time.time())

        resp = client.post(
            "/radio/now-playing",
            json={"song_id": slot["song"]["id"], "started_at": slot["slot_start"]},
        )
        assert resp.status_code == 204
        np = [c for c in fake_call if c["method"] == "track.updateNowPlaying"]
        assert len(np) == 1
        assert "timestamp" not in np[0]["params"]

    def test_scrobble_through_real_creds_and_threads(self, app, client, db, monkeypatch):
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
        with app.app_context():
            slot = radio._song_at(time.time())

        resp = client.post(
            "/radio/scrobble",
            json={"song_id": slot["song"]["id"], "started_at": slot["slot_start"]},
        )
        assert resp.status_code == 204  # not 500 — worker thread had app context
        assert any("audioscrobbler" in u for u in seen)

    def test_disabled_account_does_not_scrobble(self, app, client, db, fake_call):
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
        with app.app_context():
            slot = radio._song_at(time.time())
        client.post(
            "/radio/scrobble",
            json={"song_id": slot["song"]["id"], "started_at": slot["slot_start"]},
        )
        assert [c for c in fake_call if c["method"] == "track.scrobble"] == []


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
            cur.execute("SELECT session_key, remote_username FROM scrobble_account WHERE user_id = 2")
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["session_key"] == "SK-lastfm"
        assert rows[0]["remote_username"] == "lfm_user"


# ── Radio page flag ──────────────────────────────────────────────────


def test_radio_page_scrobble_disabled_when_logged_out(client):
    resp = client.get("/radio", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert b"window.SCROBBLE_ENABLED = false" in resp.data
