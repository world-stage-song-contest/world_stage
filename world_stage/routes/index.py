from flask import Blueprint, make_response, redirect, request, url_for

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
                  SELECT show_id FROM vote_set WHERE voter_id = %s AND result_mode = 'official'
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
