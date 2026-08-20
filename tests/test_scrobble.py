import hashlib
import string
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage import scrobble
from world_stage.routes import radio


def _add_song(db, title="Catalog Title", artist="Catalog Artist", duration=200):
    with db.cursor() as cursor:
        song_id = cursor.execute(
            """INSERT INTO song (country_id, year_id, entry_number)
               VALUES ('US', 2024, 1) RETURNING id"""
        ).fetchone()["id"]
        cursor.execute(
            """INSERT INTO song_data (
                   song_id, submitter_id, title, artist_credit_set_id,
                   video_link, duration
               ) VALUES (
                   %s, 1, %s, test_artist_credit(%s),
                   'https://media.world-stage.org/scrobble.mp4', %s
               )""",
            (song_id, title, artist, duration),
        )
    db.commit()
    return song_id


def _link(db, *, enabled=True, service="lastfm"):
    db.execute(
        """INSERT INTO scrobble_account (
               user_id, service, session_key, remote_username, enabled
           ) VALUES (2, %s, 'SK', 'remote', %s)
           ON CONFLICT (user_id, service) DO UPDATE SET enabled = EXCLUDED.enabled""",
        (service, enabled),
    )
    db.commit()


@pytest.fixture()
def calls(monkeypatch):
    recorded = []

    def fake_call(service, method, params, *, post):
        recorded.append({"service": service, "method": method, "params": params, "post": post})
        if method == "auth.getSession":
            return {"session": {"key": f"KEY-{service}", "name": f"user-{service}"}}
        return {"ok": 1}

    monkeypatch.setattr(scrobble, "_call", fake_call)
    monkeypatch.setattr(scrobble, "is_configured", lambda _service: True)
    return recorded


def test_signature_is_canonical_and_ignores_response_format():
    keys = st.text(string.ascii_lowercase, min_size=1, max_size=8)
    values = st.text(string.ascii_letters + string.digits, min_size=0, max_size=12)

    @given(params=st.dictionaries(keys, values, max_size=8), secret=values)
    def property_test(params, secret):
        without_format = {key: value for key, value in params.items() if key != "format"}
        expected_payload = (
            "".join(key + value for key, value in sorted(without_format.items())) + secret
        )
        expected = hashlib.md5(expected_payload.encode()).hexdigest()

        assert scrobble._sign(params, secret) == expected
        assert scrobble._sign({**params, "format": "anything"}, secret) == expected
        assert scrobble._sign(dict(reversed(list(params.items()))), secret) == expected

    property_test()


def test_radio_slot_validation_accepts_only_the_authoritative_recent_slot(app, client, db):
    _add_song(db)
    slot = client.get("/radio/now").get_json()
    slot_id = slot["slot_id"]

    @given(
        candidate=st.one_of(
            st.just(slot_id),
            st.integers().filter(lambda value: value != slot_id),
            st.text(max_size=12),
            st.booleans(),
            st.none(),
        )
    )
    def property_test(candidate):
        with app.app_context():
            result = radio._validate_submission(candidate)
        assert (result is not None) is (
            isinstance(candidate, int) and not isinstance(candidate, bool) and candidate == slot_id
        )
        if result is not None:
            assert result["song"]["id"] == slot["song"]["id"]

    property_test()


def test_radio_dispatch_depends_on_login_account_state_and_valid_slot(client, db, calls, login):
    _add_song(db, title="Server Title", artist="Server Artist")
    slot = client.get("/radio/now").get_json()
    login()
    _link(db)

    @settings(max_examples=12)
    @given(
        action=st.sampled_from(["now-playing", "scrobble"]),
        authenticated=st.booleans(),
        enabled=st.booleans(),
        valid_slot=st.booleans(),
        spoofed_artist=st.text(max_size=20),
    )
    def property_test(action, authenticated, enabled, valid_slot, spoofed_artist):
        calls.clear()
        db.execute("UPDATE scrobble_account SET enabled = %s WHERE user_id = 2", (enabled,))
        db.commit()
        if authenticated:
            login()
        else:
            client.delete_cookie("session")

        response = client.post(
            f"/radio/{action}",
            json={
                "slot_id": slot["slot_id"] if valid_slot else "invalid",
                "artist": spoofed_artist,
                "track": "untrusted",
            },
        )
        assert response.status_code == 204
        dispatched = [call for call in calls if call["method"].startswith("track.")]
        should_dispatch = authenticated and enabled and valid_slot
        assert len(dispatched) == int(should_dispatch)
        if should_dispatch:
            assert dispatched[0]["params"]["artist"] == "Server Artist"
            assert dispatched[0]["params"]["track"] == "Server Title"
            assert ("timestamp" in dispatched[0]["params"]) is (action == "scrobble")

    property_test()


def test_catalog_dispatch_uses_catalog_metadata_and_rejects_bad_timestamps(
    client, db, calls, login
):
    song_id = _add_song(db)
    login()
    _link(db)
    now = int(datetime.now(UTC).timestamp())

    @given(
        action=st.sampled_from(["now-playing", "scrobble"]),
        valid_song=st.booleans(),
        timestamp=st.one_of(
            st.integers(min_value=now - 60, max_value=now + 60),
            st.sampled_from(["invalid", True, None, now - 100_000, now + 100_000]),
        ),
    )
    def property_test(action, valid_song, timestamp):
        calls.clear()
        response = client.post(
            "/scrobble/now-playing" if action == "now-playing" else "/scrobble",
            json={
                "song_id": song_id if valid_song else "invalid",
                "timestamp": timestamp,
                "artist": "untrusted",
                "track": "untrusted",
            },
        )
        assert response.status_code == 204
        timestamp_valid = (
            isinstance(timestamp, int)
            and not isinstance(timestamp, bool)
            and now - 86_400 <= timestamp <= now + 300
        )
        should_dispatch = valid_song and (action == "now-playing" or timestamp_valid)
        dispatched = [call for call in calls if call["method"].startswith("track.")]
        assert len(dispatched) == int(should_dispatch)
        if should_dispatch:
            assert dispatched[0]["params"]["artist"] == "Catalog Artist"
            assert dispatched[0]["params"]["track"] == "Catalog Title"
            assert dispatched[0]["params"].get("album") is None

    property_test()


def test_account_callback_is_idempotent_for_each_service(client, db, calls, login):
    login()

    @given(service=st.sampled_from(["lastfm", "librefm"]), repeats=st.integers(1, 4))
    def property_test(service, repeats):
        db.execute("DELETE FROM scrobble_account WHERE user_id = 2")
        db.commit()
        for _ in range(repeats):
            response = client.get(f"/settings/scrobble/{service}/callback?token=generated")
            assert response.status_code in {301, 302}

        accounts = db.execute(
            """SELECT service, session_key, remote_username
               FROM scrobble_account WHERE user_id = 2"""
        ).fetchall()
        assert accounts == [
            {
                "service": service,
                "session_key": f"KEY-{service}",
                "remote_username": f"user-{service}",
            }
        ]

    property_test()
