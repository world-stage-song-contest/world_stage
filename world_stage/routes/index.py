import hashlib

from flask import Blueprint, Response, abort, make_response, redirect, request, url_for

from .. import scrobble
from ..avatar import (
    MAX_AVATAR_BYTES,
    MAX_AVATAR_DIMENSION,
    AvatarValidationError,
    default_avatar_svg,
    normalize_avatar,
)
from ..db import get_db
from ..messaging import has_unread_admin_messages, has_unread_messages
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

AVATAR_REQUEST_OVERHEAD = 64 * 1024


@bp.get("/")
@with_auth
def home(user: tuple[int, str] | None, permissions: UserPermissions):
    # Highlight the Vote tile when the signed-in user has at least one
    # open voting they haven't cast a ballot in yet — a nudge to
    # finish what they started. Anonymous visitors don't get the nudge
    # because there's no per-user vote history to check against.
    has_pending_vote = False
    has_unread = False
    has_admin_unread = False
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
        has_unread = has_unread_messages(user_id, permissions)
        if permissions.can_view_restricted:
            has_admin_unread = has_unread_admin_messages(user_id)

    is_admin = permissions.can_view_restricted

    return render_template(
        "index.html",
        has_pending_vote=has_pending_vote,
        has_unread_messages=has_unread,
        has_unread_admin_messages=has_admin_unread,
        is_admin=is_admin,
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


def _has_custom_avatar(user_id: int) -> bool:
    cursor = get_db().cursor()
    cursor.execute("SELECT 1 FROM account_avatar WHERE account_id = %s", (user_id,))
    return cursor.fetchone() is not None


def _settings_template(
    user: tuple[int, str] | None,
    *,
    settings: dict | None = None,
    message: str | None = None,
    error: str | None = None,
):
    if settings is None:
        preferences = request.cookies.get("preferences", "")
        settings = parse_cookie(preferences)

    user_id = user[0] if user else None
    return render_template(
        "settings.html",
        settings=settings,
        user=user,
        tokens=_get_user_tokens(user_id) if user_id else [],
        scrobble_services=_scrobble_services(user_id) if user_id else [],
        has_custom_avatar=_has_custom_avatar(user_id) if user_id else False,
        message=message,
        error=error,
        max_avatar_dimension=MAX_AVATAR_DIMENSION,
        max_avatar_megabytes=MAX_AVATAR_BYTES // (1024 * 1024),
    )


@bp.get("/settings")
@with_user
def settings(user: tuple[int, str] | None):
    avatar_message = {
        "updated": "Avatar updated successfully.",
        "removed": "Custom avatar removed. Your generated avatar is active.",
    }.get(request.args.get("avatar", ""))
    return _settings_template(user, message=avatar_message)


def _scrobble_services(user_id: int) -> list[dict]:
    """Per configured service, its display name and the user's linked
    row (or None) — for the settings "Scrobbling" section."""
    linked = {a["service"]: a for a in scrobble.get_accounts(user_id)}
    return [
        {"id": s, "name": scrobble.SERVICES[s]["name"], "linked": linked.get(s)}
        for s in scrobble.configured_services()
    ]


@bp.post("/settings")
@with_user
def settings_post(user: tuple[int, str] | None):
    settings = {}
    for key, value in request.form.items():
        if key not in ("token_label", "delete_token"):
            settings[key] = value

    resp = make_response(
        _settings_template(user, settings=settings, message="Settings saved successfully.")
    )
    resp.set_cookie("preferences", create_cookie(**settings), max_age=60 * 60 * 24 * 30)
    return resp


@bp.get("/avatars/<int:user_id>")
def avatar_image(user_id: int):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT account.id, account_avatar.image_data, account_avatar.media_type
        FROM account
        LEFT JOIN account_avatar ON account_avatar.account_id = account.id
        WHERE account.id = %s
        """,
        (user_id,),
    )
    avatar = cursor.fetchone()
    if avatar is None:
        abort(404)
    assert avatar is not None

    if avatar["image_data"] is None:
        data = default_avatar_svg(user_id)
        media_type = "image/svg+xml"
    else:
        data = bytes(avatar["image_data"])
        media_type = avatar["media_type"]

    response = Response(data, content_type=media_type)
    response.set_etag(hashlib.sha256(data).hexdigest())
    response.cache_control.private = True
    response.cache_control.max_age = 0
    response.cache_control.must_revalidate = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response.make_conditional(request)


@bp.post("/settings/avatar")
@require_user(redirect_to_login=True)
def update_avatar(user: tuple[int, str]):
    user_id, _username = user
    if (
        request.content_length is not None
        and request.content_length > MAX_AVATAR_BYTES + AVATAR_REQUEST_OVERHEAD
    ):
        return (
            _settings_template(user, error="Avatar files must be no larger than 2 MB."),
            413,
        )

    action = request.form.get("action", "upload")
    db = get_db()
    cursor = db.cursor()

    if action == "remove":
        cursor.execute("DELETE FROM account_avatar WHERE account_id = %s", (user_id,))
        db.commit()
        return redirect(url_for("main.settings", avatar="removed"))
    if action != "upload":
        return _settings_template(user, error="Unknown avatar action."), 400

    upload = request.files.get("avatar")
    if upload is None or not upload.filename:
        return _settings_template(user, error="Choose an image to upload."), 400

    try:
        avatar = normalize_avatar(upload)
    except AvatarValidationError as error:
        return _settings_template(user, error=str(error)), 400

    cursor.execute(
        """
        INSERT INTO account_avatar (account_id, image_data, media_type, width, height)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (account_id) DO UPDATE SET
            image_data = EXCLUDED.image_data,
            media_type = EXCLUDED.media_type,
            width = EXCLUDED.width,
            height = EXCLUDED.height,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, avatar.data, avatar.media_type, avatar.width, avatar.height),
    )
    db.commit()
    return redirect(url_for("main.settings", avatar="updated"))


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
    auth_url = scrobble.auth_redirect_url(service, cb)
    if auth_url is None:
        return redirect(url_for("main.settings"))
    return redirect(auth_url)


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
