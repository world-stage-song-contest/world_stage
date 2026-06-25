"""Optional Last.fm / Libre.fm scrobbling for the radio.

Both services speak the AudioScrobbler 2.0 protocol: an API key + shared
secret identify the app, a per-user session key (obtained via the
web-auth flow) authorises submissions, and every call is signed with an
MD5 of its sorted parameters. The two services differ only in their
endpoint URLs and which config keys hold their credentials, so they
share one code path here.

All network calls are best-effort: they run inside the request that
triggers them, so any failure is logged and swallowed rather than raised
into the response. A service whose API key/secret isn't configured is
never offered to users and never contacted.
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from .db import get_db

log = logging.getLogger(__name__)

# Per-service endpoints and the config keys that hold their credentials.
SERVICES = {
    "lastfm": {
        "name": "Last.fm",
        "ws_root": "https://ws.audioscrobbler.com/2.0/",
        "auth_url": "https://www.last.fm/api/auth/",
        "api_key_cfg": "LASTFM_API_KEY",
        "api_secret_cfg": "LASTFM_API_SECRET",
    },
    "librefm": {
        "name": "Libre.fm",
        "ws_root": "https://libre.fm/2.0/",
        "auth_url": "https://libre.fm/api/auth/",
        "api_key_cfg": "LIBREFM_API_KEY",
        "api_secret_cfg": "LIBREFM_API_SECRET",
    },
}

# Kept short: these calls block the request that triggers them.
HTTP_TIMEOUT = 6.0

ALBUM = "World Stage Radio"


# ── Configuration ────────────────────────────────────────────────────


def _creds(service: str) -> tuple[str, str] | None:
    """(api_key, api_secret) for a service, or None if not fully configured."""
    svc = SERVICES.get(service)
    if not svc:
        return None
    key = current_app.config.get(svc["api_key_cfg"]) or os.environ.get(svc["api_key_cfg"])
    secret = current_app.config.get(svc["api_secret_cfg"]) or os.environ.get(
        svc["api_secret_cfg"]
    )
    if not key or not secret:
        return None
    return key, secret


def is_configured(service: str) -> bool:
    return _creds(service) is not None


def configured_services() -> list[str]:
    return [s for s in SERVICES if is_configured(s)]


def auth_redirect_url(service: str, callback: str) -> str | None:
    """The URL to send a user to so they can authorise the app, or None
    if the service isn't configured. ``callback`` is where the service
    sends them back (it appends ``?token=...``)."""
    creds = _creds(service)
    if creds is None:
        return None
    api_key, _secret = creds
    auth_url = SERVICES[service]["auth_url"]
    return f"{auth_url}?{urllib.parse.urlencode({'api_key': api_key, 'cb': callback})}"


# ── AudioScrobbler protocol ──────────────────────────────────────────


def _sign(params: dict[str, str], secret: str) -> str:
    """AudioScrobbler signature: md5 of every name+value concatenated in
    key order (excluding ``format``), then the shared secret appended."""
    items = sorted((k, v) for k, v in params.items() if k != "format")
    raw = "".join(f"{k}{v}" for k, v in items) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _call(service: str, method: str, params: dict, *, post: bool) -> dict | None:
    """Make one signed API call. Returns the parsed JSON, or None on any
    failure (unconfigured, network error, API error, bad JSON)."""
    creds = _creds(service)
    if creds is None:
        return None
    api_key, secret = creds

    p = {k: str(v) for k, v in params.items() if v is not None and v != ""}
    p["method"] = method
    p["api_key"] = api_key
    p["api_sig"] = _sign(p, secret)  # signed before format is added
    p["format"] = "json"

    root = SERVICES[service]["ws_root"]
    try:
        if post:
            req = urllib.request.Request(
                root, data=urllib.parse.urlencode(p).encode("utf-8"), method="POST"
            )
        else:
            req = urllib.request.Request(root + "?" + urllib.parse.urlencode(p), method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        log.warning("scrobble %s %s failed: %s", service, method, e)
        return None
    if isinstance(out, dict) and "error" in out:
        log.warning(
            "scrobble %s %s error %s: %s",
            service,
            method,
            out.get("error"),
            out.get("message"),
        )
        return None
    return out


def get_session(service: str, token: str) -> dict | None:
    """Exchange an auth token for a long-lived session key.
    Returns {'session_key': ..., 'username': ...} or None."""
    out = _call(service, "auth.getSession", {"token": token}, post=False)
    if not out:
        return None
    sess = out.get("session") or {}
    key = sess.get("key")
    if not key:
        return None
    return {"session_key": key, "username": sess.get("name")}


def now_playing(service, session_key, artist, track, duration=None) -> bool:
    out = _call(
        service,
        "track.updateNowPlaying",
        {
            "artist": artist,
            "track": track,
            "sk": session_key,
            "duration": int(duration) if duration else None,
        },
        post=True,
    )
    return out is not None


def scrobble(service, session_key, artist, track, timestamp, album=None, duration=None) -> bool:
    out = _call(
        service,
        "track.scrobble",
        {
            "artist": artist,
            "track": track,
            "timestamp": int(timestamp),
            "sk": session_key,
            "album": album,
            "duration": int(duration) if duration else None,
        },
        post=True,
    )
    return out is not None


# ── Per-user linked accounts ─────────────────────────────────────────


def get_accounts(user_id: int) -> list[dict]:
    """All linked accounts for a user (including disabled and accounts
    whose service is no longer configured), for the settings page."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, service, session_key, remote_username, enabled, last_scrobbled_at
        FROM scrobble_account WHERE user_id = %s
        """,
        (user_id,),
    )
    return cursor.fetchall()


def get_enabled_accounts(user_id: int) -> list[dict]:
    """Enabled accounts whose service is currently configured — i.e. the
    ones that should actually receive scrobbles."""
    return [
        a
        for a in get_accounts(user_id)
        if a["enabled"] and is_configured(a["service"])
    ]


def has_enabled_account(user_id: int) -> bool:
    return bool(get_enabled_accounts(user_id))


def upsert_account(user_id: int, service: str, session_key: str, remote_username: str | None):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO scrobble_account (user_id, service, session_key, remote_username, enabled)
        VALUES (%s, %s, %s, %s, true)
        ON CONFLICT (user_id, service) DO UPDATE
            SET session_key = EXCLUDED.session_key,
                remote_username = EXCLUDED.remote_username,
                enabled = true
        """,
        (user_id, service, session_key, remote_username),
    )
    db.commit()


def _mark_scrobbled(account_id: int):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE scrobble_account SET last_scrobbled_at = CURRENT_TIMESTAMP WHERE id = %s",
        (account_id,),
    )
    db.commit()


def send_to_all(user_id, *, artist, track, timestamp=None, duration=None, album=ALBUM):
    """Dispatch a now-playing update (timestamp=None) or a scrobble
    (timestamp set) to every enabled, configured account for the user.

    Best-effort: services are contacted in parallel so total latency is
    bounded by the slowest single call rather than their sum.
    """
    accounts = get_enabled_accounts(user_id)
    if not accounts:
        return

    # Worker threads need their own application context — current_app
    # (read by _creds) is bound to the request thread, not the pool's.
    app = current_app._get_current_object()

    def one(account):
        with app.app_context():
            if timestamp is None:
                ok = now_playing(
                    account["service"], account["session_key"], artist, track, duration
                )
            else:
                ok = scrobble(
                    account["service"], account["session_key"], artist, track, timestamp, album, duration
                )
        return account["id"] if (ok and timestamp is not None) else None

    with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
        scrobbled = [r for r in pool.map(one, accounts) if r is not None]
    for account_id in scrobbled:
        _mark_scrobbled(account_id)


# ── CLI ──────────────────────────────────────────────────────────────


@click.command("scrobble-doctor")
@with_appcontext
def scrobble_doctor_command():
    """Report which scrobbling services are configured on this instance."""
    for service, svc in SERVICES.items():
        state = "configured" if is_configured(service) else "not configured"
        click.echo(f"{svc['name']} ({service}): {state}")


def init_app(app: Flask):
    app.cli.add_command(scrobble_doctor_command)
