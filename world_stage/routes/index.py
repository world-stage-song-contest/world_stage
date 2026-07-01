import os.path
import re

from flask import Blueprint, current_app, make_response, redirect, request, send_file, url_for

from .. import scrobble
from ..db import get_db
from ..utils import (
    UserPermissions,
    create_cookie,
    generate_api_token,
    get_user_id_from_session,
    parse_cookie,
    render_template,
    require_user,
    with_auth,
    with_user,
)

# Lazy cache: 2-letter code → 3-letter code (populated on first miss).
_cc2_to_cc3: dict[str, str] = {}
_cc3_cache_loaded = False


def _ensure_cc3_cache():
    global _cc3_cache_loaded
    if _cc3_cache_loaded:
        return
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, cc3 FROM country")
    for row in cursor.fetchall():
        _cc2_to_cc3[row["id"].upper()] = row["cc3"].upper()
    _cc3_cache_loaded = True


def _get_cc3(cc2: str) -> str | None:
    """Return the 3-letter code for a 2-letter code, or None."""
    _ensure_cc3_cache()
    return _cc2_to_cc3.get(cc2.upper())


bp = Blueprint("main", __name__, url_prefix="/")


@bp.get("/")
@with_auth
def home(user: tuple[int, str] | None, permissions: UserPermissions):
    # Highlight the Vote tile when the signed-in user has at least one
    # open voting they haven't cast a ballot in yet — a nudge to
    # finish what they started. Anonymous visitors don't get the nudge
    # because there's no per-user vote history to check against.
    has_pending_vote = False
    if user:
        user_id = user[0]
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT 1 FROM show
            WHERE voting_opens <= CURRENT_TIMESTAMP
              AND (voting_closes IS NULL OR voting_closes >= CURRENT_TIMESTAMP)
              AND id NOT IN (
                  SELECT show_id FROM vote_set WHERE voter_id = %s
              )
            LIMIT 1
            """,
            (user_id,),
        )
        has_pending_vote = cursor.fetchone() is not None

    is_admin = permissions.can_view_restricted

    return render_template(
        "index.html", has_pending_vote=has_pending_vote, is_admin=is_admin
    )


@bp.get("/favicon.ico")
def favicon():
    return send_file("files/favicons/favicon.ico")


@bp.get("/robots.txt")
def robots():
    return send_file("files/robots.txt")


@bp.get("/favicons/<name>")
def favicons(name: str):
    valid_names = [
        "favicon.ico",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "favicon.svg",
        "apple-touch-icon.png",
        "android-chrome-192x192.png",
        "android-chrome-512x512.png",
        "site.webmanifest",
    ]
    if name not in valid_names:
        return render_template("error.html", error="Invalid favicon name."), 404

    file_path = os.path.join(current_app.root_path, "files", "favicons", name)
    if not os.path.exists(file_path):
        return render_template("error.html", error="Favicon not found."), 404

    resp = make_response(send_file(file_path))
    match name:
        case "favicon.ico":
            resp.headers["Content-Type"] = "image/x-icon"
        case "favicon-16x16.png" | "favicon-32x32.png":
            resp.headers["Content-Type"] = "image/png"
        case "favicon.svg":
            resp.headers["Content-Type"] = "image/svg+xml"
        case "apple-touch-icon.png":
            resp.headers["Content-Type"] = "image/png"
        case "android-chrome-192x192.png" | "android-chrome-512x512.png":
            resp.headers["Content-Type"] = "image/png"
        case "site.webmanifest":
            resp.headers["Content-Type"] = "application/manifest+json"
    resp.cache_control.max_age = 60 * 60 * 24 * 30
    resp.cache_control.public = True
    resp.cache_control.immutable = True
    return resp


@bp.get("/error")
def error():
    errors = request.args.getlist("error")
    return render_template("error.html", errors=errors), 400


@bp.get("/error.json")
def error_json():
    errors = request.args.getlist("error")
    return {"errors": errors}, 400


def _get_user_tokens(user_id: int) -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, label, created_at, last_used_at
        FROM api_token WHERE user_id = %s
        ORDER BY created_at DESC
    """,
        (user_id,),
    )
    return cursor.fetchall()


@bp.get("/settings")
@with_user
def settings(user: tuple[int, str] | None):
    preferences = request.cookies.get("preferences", "")
    settings = parse_cookie(preferences)

    tokens = _get_user_tokens(user[0]) if user else []
    scrobble_services = _scrobble_services(user[0]) if user else []

    return render_template(
        "settings.html",
        settings=settings,
        user=user,
        tokens=tokens,
        scrobble_services=scrobble_services,
    )


def _scrobble_services(user_id: int) -> list[dict]:
    """Per configured service, its display name and the user's linked
    row (or None) — for the settings "Scrobbling" section."""
    linked = {a["service"]: a for a in scrobble.get_accounts(user_id)}
    return [
        {"id": s, "name": scrobble.SERVICES[s]["name"], "linked": linked.get(s)}
        for s in scrobble.configured_services()
    ]


@bp.post("/settings")
def settings_post():
    settings = {}
    for key, value in request.form.items():
        if key not in ("token_label", "delete_token"):
            settings[key] = value

    resp = make_response(
        render_template("settings.html", settings=settings, message="Settings saved successfully.")
    )
    resp.set_cookie("preferences", create_cookie(**settings), max_age=60 * 60 * 24 * 30)
    return resp


@bp.post("/settings/token")
@require_user(redirect_to_login=True)
def create_token(user: tuple[int, str]):
    user_id, _ = user
    label = request.form.get("token_label", "").strip()

    plaintext, token_hash = generate_api_token()

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO api_token (user_id, token_hash, label)
        VALUES (%s, %s, %s)
    """,
        (user_id, token_hash, label),
    )
    db.commit()

    preferences = request.cookies.get("preferences", "")
    settings = parse_cookie(preferences)
    tokens = _get_user_tokens(user_id)

    return render_template(
        "settings.html",
        settings=settings,
        user=user,
        tokens=tokens,
        new_token=plaintext,
        message="API token created. Copy it now — it won't be shown again.",
    )


@bp.post("/settings/token/delete")
@require_user(redirect_to_login=True)
def delete_token(user: tuple[int, str]):
    user_id, _ = user
    token_id = request.form.get("token_id", type=int)

    if token_id:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            DELETE FROM api_token WHERE id = %s AND user_id = %s
        """,
            (token_id, user_id),
        )
        db.commit()

    return redirect(url_for("main.settings"))


def _scrobble_user_or_login():
    session_id = request.cookies.get("session")
    user = get_user_id_from_session(session_id) if session_id else None
    return user[0] if user else None


@bp.get("/settings/scrobble/<service>/connect")
def scrobble_connect(service: str):
    user_id = _scrobble_user_or_login()
    if user_id is None:
        return redirect(url_for("session.login"))
    if not scrobble.is_configured(service):
        return redirect(url_for("main.settings"))

    cb = url_for("main.scrobble_callback", service=service, _external=True)
    return redirect(scrobble.auth_redirect_url(service, cb))


@bp.get("/settings/scrobble/<service>/callback")
def scrobble_callback(service: str):
    user_id = _scrobble_user_or_login()
    if user_id is None:
        return redirect(url_for("session.login"))
    if not scrobble.is_configured(service):
        return redirect(url_for("main.settings"))

    token = request.args.get("token")
    if token:
        sess = scrobble.get_session(service, token)
        if sess:
            scrobble.upsert_account(user_id, service, sess["session_key"], sess["username"])
    return redirect(url_for("main.settings"))


@bp.post("/settings/scrobble/<service>/disconnect")
def scrobble_disconnect(service: str):
    user_id = _scrobble_user_or_login()
    if user_id is None:
        return redirect(url_for("session.login"))

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM scrobble_account WHERE user_id = %s AND service = %s",
        (user_id, service),
    )
    db.commit()
    return redirect(url_for("main.settings"))


@bp.post("/settings/scrobble/<service>/toggle")
def scrobble_toggle(service: str):
    user_id = _scrobble_user_or_login()
    if user_id is None:
        return redirect(url_for("session.login"))

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE scrobble_account SET enabled = NOT enabled WHERE user_id = %s AND service = %s",
        (user_id, service),
    )
    db.commit()
    return redirect(url_for("main.settings"))


@bp.get("/flag")
def flag_index():
    return render_template("error.html", error="Do not access this page."), 401


@bp.get("/flag/<country>.svg")
def flag(country: str):
    country = country.upper()
    type = request.args.get("t", "rect")

    if type not in ["rect", "square"]:
        type = "rect"

    width = request.args.get("w", 40)
    try:
        width = int(width)
    except ValueError:
        width = 40

    size = ""
    if width <= 40:
        size = "small"

    variant = request.args.get("v") or None
    if variant and not re.fullmatch(r"[A-Za-z0-9_-]+", variant):
        variant = None

    root = current_app.root_path
    flags_root = os.path.join(root, "files", "flags")

    # Try the code as-is first (2-letter), then fall back to the 3-letter
    # equivalent from the database, then to the XX placeholder.
    candidates = [country]
    cc3 = _get_cc3(country)
    if cc3 and cc3 != country:
        candidates.append(cc3)

    file = None
    for candidate in candidates:
        if variant:
            path = os.path.join(flags_root, candidate, variant, f"{type}.svg")
            if os.path.exists(path):
                file = path
                break
        path = os.path.join(flags_root, candidate, f"{type}-{size}.svg")
        if os.path.exists(path):
            file = path
            break
        path = os.path.join(flags_root, candidate, f"{type}.svg")
        if os.path.exists(path):
            file = path
            break

    if not file:
        file = os.path.join(flags_root, "XX", f"{type}.svg")

    resp = make_response(send_file(file))
    resp.headers["Content-Type"] = "image/svg+xml"
    resp.cache_control.max_age = 60 * 60 * 24 * 30
    resp.cache_control.public = True
    resp.cache_control.immutable = True
    return resp
