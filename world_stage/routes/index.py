import hashlib

from flask import Blueprint, Response, abort, current_app, make_response, redirect, request, url_for

from .. import scrobble
from ..avatar import (
    MAX_AVATAR_BYTES,
    MAX_AVATAR_DIMENSION,
    AvatarValidationError,
    default_avatar_svg,
    normalize_avatar,
)
from ..db import get_db
from ..email import validate_email
from ..messaging import (
    banner_conversations,
    has_unread_admin_messages,
    has_unread_messages,
)
from ..show_notifications import grouped_timezones, valid_timezone
from ..user_settings import get_user_settings, setting, update_user_settings
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
    # Highlight the Vote tile only when one of the signed-in user's songs is
    # competing in an open main-contest show and they have not cast that
    # show's ballot. Unrelated shows and national finals do not create a
    # personal voting obligation.
    has_pending_vote = False
    has_unread = False
    has_admin_unread = False
    banners = []
    if user:
        user_id = user[0]
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM show
            JOIN song_show ON song_show.show_id = show.id
            JOIN current_song AS song ON song.id = song_show.song_id
            WHERE show.voting_opens <= CURRENT_TIMESTAMP
              AND (show.voting_closes IS NULL OR show.voting_closes >= CURRENT_TIMESTAMP)
              AND show.national_final_id IS NULL
              AND song.submitter_id = %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM vote_set
                  WHERE vote_set.show_id = show.id
                    AND vote_set.voter_id = %s
                    AND vote_set.result_mode = 'official'
              )
            LIMIT 1
            """,
            (user_id, user_id),
        )
        has_pending_vote = cursor.fetchone() is not None
        has_unread = has_unread_messages(user_id, permissions)
        banners = banner_conversations(user_id)
        if permissions.can_moderate:
            has_admin_unread = has_unread_admin_messages(user_id)

    is_admin = permissions.can_view_restricted
    is_moderator = permissions.can_moderate

    return render_template(
        "index.html",
        has_pending_vote=has_pending_vote,
        has_unread_messages=has_unread,
        has_unread_admin_messages=has_admin_unread,
        banner_conversations=banners,
        is_admin=is_admin,
        is_moderator=is_moderator,
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


def _user_email(user_id: int) -> str:
    cursor = get_db().cursor()
    cursor.execute("SELECT email FROM account WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    return row["email"] or "" if row else ""


def _show_notification_preferences(user_id: int) -> dict:
    settings = get_user_settings(user_id)
    return {
        "placeholder_claims": setting(
            settings, "notifications", "placeholder_claims", default=False
        )
        is True,
        "timezone": setting(settings, "notifications", "show_reminders", "timezone", default=""),
        "participating_shows": setting(
            settings,
            "notifications",
            "show_reminders",
            "participating_shows",
            default=False,
        )
        is True,
        "all_shows": setting(
            settings, "notifications", "show_reminders", "all_shows", default=False
        )
        is True,
    }


def _website_settings(user_id: int | None) -> dict:
    cookie_value = request.cookies.get("preferences")
    cookie = parse_cookie(cookie_value or "")
    cookie_theme = cookie["theme"] if cookie["theme"] in {"auto", "light", "dark"} else "auto"
    if user_id is None:
        return {"theme": cookie_theme, "hide_message_avatars": False}
    settings = get_user_settings(user_id)
    account_theme = setting(settings, "theme", default="auto")
    if account_theme not in {"auto", "light", "dark"}:
        account_theme = "auto"
    return {
        "theme": cookie_theme if cookie_value is not None else account_theme,
        "hide_message_avatars": setting(settings, "messages", "hide_avatars", default=False)
        is True,
    }


def _settings_template(
    user: tuple[int, str] | None,
    *,
    settings: dict | None = None,
    message: str | None = None,
    error: str | None = None,
    show_notification_preferences: dict | None = None,
    new_token: str | None = None,
):
    user_id = user[0] if user else None
    if settings is None:
        settings = _website_settings(user_id)
    if user_id and show_notification_preferences is None:
        show_notification_preferences = _show_notification_preferences(user_id)
    return render_template(
        "settings.html",
        settings=settings,
        user=user,
        tokens=_get_user_tokens(user_id) if user_id else [],
        scrobble_services=_scrobble_services(user_id) if user_id else [],
        has_custom_avatar=_has_custom_avatar(user_id) if user_id else False,
        email=_user_email(user_id) if user_id else "",
        show_notification_preferences=show_notification_preferences,
        show_notification_timezone_groups=grouped_timezones(
            current_app.config["SHOW_NOTIFICATION_TIMEZONES"]
        )
        if user_id
        else (),
        message=message,
        error=error,
        new_token=new_token,
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
    theme = request.form.get("theme", "auto")
    if theme not in {"auto", "light", "dark"}:
        return _settings_template(user, error="Choose a valid theme."), 400

    settings = {
        "theme": theme,
        "hide_message_avatars": request.form.get("hide_message_avatars") == "true",
    }
    if user:
        db = get_db()
        update_user_settings(
            db.cursor(),
            user[0],
            {
                "theme": theme,
                "messages": {"hide_avatars": settings["hide_message_avatars"]},
            },
        )
        db.commit()

    resp = make_response(
        _settings_template(user, settings=settings, message="Settings saved successfully.")
    )
    resp.set_cookie("preferences", create_cookie(theme=theme), max_age=60 * 60 * 24 * 30)
    return resp


@bp.post("/settings/email")
@require_user(redirect_to_login=True)
def update_email(user: tuple[int, str]):
    user_id, _username = user
    email = request.form.get("email", "").strip()
    valid, error = validate_email(email)
    if not valid:
        return _settings_template(user, error=error), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE account SET email = %s WHERE id = %s", (email or None, user_id))
    if email:
        cursor.execute(
            """
            UPDATE conversation_participant
            SET email_notifications = true
            FROM conversation
            WHERE conversation.id = conversation_participant.conversation_id
              AND conversation_participant.account_id = %s
              AND (conversation.created_by_admin OR conversation.system_conversation)
            """,
            (user_id,),
        )
    db.commit()
    return _settings_template(
        user,
        message="Email address updated." if email else "Email address removed.",
    )


@bp.post("/settings/show-notifications")
@require_user(redirect_to_login=True)
def update_show_notifications(user: tuple[int, str]):
    user_id, _username = user
    preferences = {
        "placeholder_claims": request.form.get("placeholder_claims") == "true",
        "timezone": request.form.get("timezone", "").strip(),
        "participating_shows": request.form.get("participating_shows") == "true",
        "all_shows": request.form.get("all_shows") == "true",
    }
    if not valid_timezone(
        preferences["timezone"], current_app.config["SHOW_NOTIFICATION_TIMEZONES"]
    ):
        return (
            _settings_template(
                user,
                show_notification_preferences=preferences,
                error="Choose a valid IANA timezone, such as Europe/Warsaw.",
            ),
            400,
        )
    if (
        preferences["placeholder_claims"]
        or preferences["participating_shows"]
        or preferences["all_shows"]
    ) and not _user_email(user_id):
        return (
            _settings_template(
                user,
                show_notification_preferences=preferences,
                error="Add an email address before enabling email notifications.",
            ),
            400,
        )

    db = get_db()
    update_user_settings(
        db.cursor(),
        user_id,
        {
            "notifications": {
                "placeholder_claims": preferences["placeholder_claims"],
                "show_reminders": {
                    "timezone": preferences["timezone"],
                    "participating_shows": preferences["participating_shows"],
                    "all_shows": preferences["all_shows"],
                },
            }
        },
    )
    db.commit()
    return _settings_template(user, message="Show notification settings updated.")


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

    return _settings_template(
        user,
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
