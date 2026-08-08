import datetime
import hashlib
import typing
import urllib.parse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from flask import Blueprint, current_app, redirect, request, url_for

from ..db import get_db
from ..messaging import mark_conversation_read, message_preview, notify_new_message
from ..utils import (
    UserPermissions,
    get_markdown_parser,
    parse_cookie,
    render_template,
    with_auth,
)

bp = Blueprint("messages", __name__, url_prefix="/messages")

MAX_SUBJECT_LENGTH = 200
MAX_MESSAGE_LENGTH = 5000
INBOX_PAGE_SIZE = 50
THREAD_PAGE_SIZE = 100
SEARCH_PAGE_SIZE = 50
DATE_MODES = {"exact", "before", "after", "between"}


def _login_required(user: tuple[int, str] | None):
    if user is None:
        return redirect(url_for("session.login"))
    return None


def _positive_page(raw: str | None) -> int:
    try:
        return max(1, int(raw or "1"))
    except ValueError:
        return 1


def _validate_subject(value: str) -> str | None:
    subject = value.strip()
    if not subject:
        return "Subject is required."
    if len(subject) > MAX_SUBJECT_LENGTH:
        return f"Subject must be at most {MAX_SUBJECT_LENGTH} characters."
    return None


def _validate_body(value: str) -> str | None:
    if not value.strip():
        return "Message is required."
    if len(value) > MAX_MESSAGE_LENGTH:
        return f"Message must be at most {MAX_MESSAGE_LENGTH} characters."
    return None


def _parse_participant_ids(
    values: list[str],
    excluded_user_id: int | None,
) -> tuple[list[int], str | None]:
    participant_ids: set[int] = set()
    try:
        for value in values:
            participant_id = int(value)
            if participant_id > 0 and participant_id != excluded_user_id:
                participant_ids.add(participant_id)
    except ValueError:
        return [], "Invalid conversation participant."

    if not participant_ids:
        return [], "Select at least one conversation participant."
    return sorted(participant_ids), None


def _available_recipients(excluded_user_id: int | None) -> list[dict]:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, username
        FROM account
        WHERE (%s::bigint IS NULL OR id <> %s::bigint) AND approved
        ORDER BY LOWER(username), id
        """,
        (excluded_user_id, excluded_user_id),
    )
    return cursor.fetchall()


def _valid_recipient_ids(recipient_ids: list[int]) -> set[int]:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id
        FROM account
        WHERE id = ANY(%s) AND approved
        """,
        (recipient_ids,),
    )
    return {row["id"] for row in cursor.fetchall()}


def _conversation_for_user(
    conversation_id: int,
    user_id: int,
    permissions: UserPermissions,
) -> dict | None:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT conversation.id, conversation.subject,
               conversation.admin_accessible, conversation.created_by_admin,
               conversation.system_conversation, conversation.created_at,
               owner.account_id AS owner_account_id,
               owner.username AS owner_username,
               current_participant.role AS participant_role,
               current_participant.email_notifications,
               current_participant.suppress_unread_highlight,
               current_participant.pinned,
               current_participant.account_id IS NOT NULL AS is_participant
        FROM conversation
        LEFT JOIN conversation_participant AS current_participant
          ON current_participant.conversation_id = conversation.id
         AND current_participant.account_id = %s
        LEFT JOIN LATERAL (
            SELECT owner_participant.account_id, account.username
            FROM conversation_participant AS owner_participant
            JOIN account ON account.id = owner_participant.account_id
            WHERE owner_participant.conversation_id = conversation.id
              AND owner_participant.role = 'owner'
        ) AS owner ON true
        WHERE conversation.id = %s
          AND (
              current_participant.account_id IS NOT NULL
              OR (conversation.admin_accessible AND %s)
          )
        """,
        (
            user_id,
            conversation_id,
            permissions.can_view_restricted,
        ),
    )
    return cursor.fetchone()


def _can_edit_conversation(
    conversation: dict,
    user_id: int,
    permissions: UserPermissions,
) -> bool:
    if conversation["system_conversation"]:
        return False
    return conversation["participant_role"] == "owner" or (
        conversation["created_by_admin"] and permissions.can_view_restricted
    )


def _can_leave_conversation(conversation: dict) -> bool:
    """Only invited participants can leave; owners and admin actors cannot."""
    return conversation["participant_role"] == "participant"


def _conversation_participants(conversation_id: int, user_id: int) -> list[dict]:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT account.id, account.username,
               conversation_participant.role,
               conversation_participant.email_notifications,
               account.id = %s AS is_current_user
        FROM conversation_participant
        JOIN account ON account.id = conversation_participant.account_id
        WHERE conversation_participant.conversation_id = %s
        ORDER BY LOWER(account.username), account.id
        """,
        (user_id, conversation_id),
    )
    return cursor.fetchall()


def _sender_colour(sender_id: int) -> str:
    """Return a stable 24-bit colour hash for an account ID."""
    digest = hashlib.sha256(str(sender_id).encode("ascii")).hexdigest()
    return f"#{digest[:6]}"


def _show_message_avatars() -> bool:
    preferences = parse_cookie(request.cookies.get("preferences", ""))
    return preferences["hide_message_avatars"] != "true"


def _message_rows(
    conversation_id: int,
    page: int,
    current_user_id: int,
) -> tuple[list[dict], bool]:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT message.id, message.sender_id, message.sender_kind,
               message.body, message.created_at, account.username AS sender_username
        FROM message
        LEFT JOIN account ON account.id = message.sender_id
        WHERE message.conversation_id = %s
        ORDER BY message.created_at DESC, message.id DESC
        LIMIT %s OFFSET %s
        """,
        (conversation_id, THREAD_PAGE_SIZE + 1, (page - 1) * THREAD_PAGE_SIZE),
    )
    rows = cursor.fetchall()
    has_older = len(rows) > THREAD_PAGE_SIZE
    rows = rows[:THREAD_PAGE_SIZE]
    rows.reverse()

    markdown = get_markdown_parser()
    for row in rows:
        row["rendered_body"] = markdown.renderInline(row["body"])
        row["is_current_user"] = row["sender_id"] == current_user_id
        row["sender_colour"] = (
            _sender_colour(row["sender_id"]) if row["sender_id"] is not None else None
        )
    return rows, has_older


def _render_new(
    user_id: int,
    permissions: UserPermissions,
    *,
    error: str | None = None,
    values: dict | None = None,
    status: int = 200,
):
    if values is None:
        cursor = get_db().cursor()
        cursor.execute(
            "SELECT NULLIF(BTRIM(email), '') IS NOT NULL AS has_email FROM account WHERE id = %s",
            (user_id,),
        )
        account = cursor.fetchone()
        values = {
            "email_notifications": bool(
                permissions.can_view_restricted and account and account["has_email"]
            )
        }
    return (
        render_template(
            "messages/new.html",
            recipients=_available_recipients(user_id),
            error=error,
            values=values,
            created_by_admin=permissions.can_view_restricted,
            max_subject_length=MAX_SUBJECT_LENGTH,
            max_message_length=MAX_MESSAGE_LENGTH,
        ),
        status,
    )


def _edit_values(conversation: dict, participants: list[dict]) -> dict:
    fixed_owner_id = conversation["owner_account_id"]
    return {
        "subject": conversation["subject"],
        "participant_ids": [
            str(participant["id"])
            for participant in participants
            if participant["id"] != fixed_owner_id
        ],
        "admin_accessible": conversation["admin_accessible"],
    }


def _render_edit(
    conversation: dict,
    *,
    values: dict,
    is_admin: bool,
    error: str | None = None,
    status: int = 200,
):
    excluded_user_id = conversation["owner_account_id"]
    return (
        render_template(
            "messages/edit.html",
            conversation=conversation,
            recipients=_available_recipients(excluded_user_id),
            values=values,
            is_admin=is_admin,
            error=error,
            max_subject_length=MAX_SUBJECT_LENGTH,
        ),
        status,
    )


@bp.get("")
@with_auth
def inbox(user: tuple[int, str] | None, permissions: UserPermissions):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    page = _positive_page(request.args.get("page"))

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT conversation.id, conversation.subject,
               conversation.admin_accessible, conversation.system_conversation,
               conversation.created_at,
               participants.usernames AS participant_usernames,
               latest.id AS latest_message_id,
               latest.body AS latest_message_body,
               latest.created_at AS latest_message_at,
               latest.sender_kind AS latest_sender_kind,
               latest.sender_username AS latest_sender_username,
               unread.unread_count,
               current_participant.account_id IS NOT NULL AS is_participant,
               COALESCE(
                   current_participant.suppress_unread_highlight,
                   false
               ) AS suppress_unread_highlight,
               COALESCE(current_participant.pinned, false) AS pinned
        FROM conversation
        LEFT JOIN conversation_participant AS current_participant
          ON current_participant.conversation_id = conversation.id
         AND current_participant.account_id = %s
        LEFT JOIN conversation_read_state
          ON conversation_read_state.conversation_id = conversation.id
         AND conversation_read_state.account_id = %s
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS unread_count
            FROM message AS unread_message
            WHERE unread_message.conversation_id = conversation.id
              AND unread_message.id > COALESCE(
                  conversation_read_state.last_read_message_id,
                  0
              )
              AND unread_message.sender_id IS DISTINCT FROM %s
        ) AS unread ON true
        LEFT JOIN LATERAL (
            SELECT ARRAY_AGG(account.username ORDER BY LOWER(account.username), account.id)
                   AS usernames
            FROM conversation_participant
            JOIN account ON account.id = conversation_participant.account_id
            WHERE conversation_participant.conversation_id = conversation.id
        ) AS participants ON true
        LEFT JOIN LATERAL (
            SELECT message.id, message.body, message.created_at, message.sender_kind,
                   account.username AS sender_username
            FROM message
            LEFT JOIN account ON account.id = message.sender_id
            WHERE message.conversation_id = conversation.id
            ORDER BY message.created_at DESC, message.id DESC
            LIMIT 1
        ) AS latest ON true
        WHERE current_participant.account_id IS NOT NULL
           OR (conversation.admin_accessible AND %s)
        ORDER BY COALESCE(current_participant.pinned, false) DESC,
                 COALESCE(latest.created_at, conversation.created_at) DESC,
                 conversation.id DESC
        LIMIT %s OFFSET %s
        """,
        (
            user_id,
            user_id,
            user_id,
            permissions.can_view_restricted,
            INBOX_PAGE_SIZE + 1,
            (page - 1) * INBOX_PAGE_SIZE,
        ),
    )
    conversations = cursor.fetchall()
    has_next = len(conversations) > INBOX_PAGE_SIZE
    conversations = conversations[:INBOX_PAGE_SIZE]
    for conversation in conversations:
        if conversation["latest_message_body"]:
            conversation["latest_message_body"] = message_preview(
                conversation["latest_message_body"]
            )

    return render_template(
        "messages/inbox.html",
        conversations=conversations,
        is_admin=permissions.can_view_restricted,
        page=page,
        has_next=has_next,
    )


@bp.get("/new")
@with_auth
def new(user: tuple[int, str] | None, permissions: UserPermissions):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    return _render_new(user[0], permissions)[0]


@bp.post("/new")
@with_auth
def new_post(user: tuple[int, str] | None, permissions: UserPermissions):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user

    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "")
    participant_values = request.form.getlist("participant_id")
    created_by_admin = permissions.can_view_restricted
    admin_accessible = created_by_admin or (
        request.form.get("admin_accessible", "").lower()
        in {"1", "true", "yes", "on"}
    )
    values = {
        "subject": subject,
        "body": body,
        "participant_ids": participant_values,
        "admin_accessible": admin_accessible,
        "email_notifications": request.form.get("email_notifications", "").lower()
        in {"1", "true", "yes", "on"},
    }

    error = _validate_subject(subject) or _validate_body(body)
    recipient_ids, participant_error = _parse_participant_ids(participant_values, user_id)
    error = error or participant_error
    if not error and _valid_recipient_ids(recipient_ids) != set(recipient_ids):
        error = "One or more selected participants are unavailable."
    if error:
        return _render_new(
            user_id,
            permissions,
            error=error,
            values=values,
            status=400,
        )

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO conversation (
                subject, admin_accessible, created_by_admin
            )
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (subject, admin_accessible, created_by_admin),
        )
        conversation = cursor.fetchone()
        assert conversation is not None
        conversation_id = conversation["id"]

        participant_ids = [user_id, *recipient_ids]
        cursor.executemany(
            """
            INSERT INTO conversation_participant (
                conversation_id, account_id, role, email_notifications
            )
            VALUES (%s, %s, %s, %s)
            """,
            [
                (
                    conversation_id,
                    participant_id,
                    "owner" if participant_id == user_id else "participant",
                    (
                        values["email_notifications"]
                        if participant_id == user_id
                        else created_by_admin
                    ),
                )
                for participant_id in participant_ids
            ],
        )
        sender_kind = "admin" if created_by_admin else "participant"
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, user_id, sender_kind, body),
        )
        inserted = cursor.fetchone()
        assert inserted is not None
        message_id = inserted["id"]
        db.commit()
    except psycopg.Error:
        db.rollback()
        current_app.logger.exception("Could not create conversation")
        return _render_new(
            user_id,
            permissions,
            error="The conversation could not be created.",
            values=values,
            status=400,
        )

    notify_new_message(conversation_id, message_id)
    return redirect(url_for("messages.thread", conversation_id=conversation_id))


@bp.get("/<int:conversation_id>/edit")
@with_auth
def edit(
    conversation_id: int,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    conversation = _conversation_for_user(conversation_id, user_id, permissions)
    if conversation is None:
        return render_template("error.html", error="Conversation not found"), 404
    if not _can_edit_conversation(conversation, user_id, permissions):
        return render_template("error.html", error="Conversation cannot be edited"), 403

    participants = _conversation_participants(conversation_id, user_id)
    return _render_edit(
        conversation,
        values=_edit_values(conversation, participants),
        is_admin=permissions.can_view_restricted,
    )[0]


@bp.post("/<int:conversation_id>/edit")
@with_auth
def edit_post(
    conversation_id: int,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    conversation = _conversation_for_user(conversation_id, user_id, permissions)
    if conversation is None:
        return render_template("error.html", error="Conversation not found"), 404
    if not _can_edit_conversation(conversation, user_id, permissions):
        return render_template("error.html", error="Conversation cannot be edited"), 403

    subject = request.form.get("subject", "").strip()
    participant_values = request.form.getlist("participant_id")
    excluded_user_id = conversation["owner_account_id"]
    participant_ids, participant_error = _parse_participant_ids(
        participant_values,
        excluded_user_id,
    )
    admin_accessible = conversation["created_by_admin"] or (
        request.form.get("admin_accessible", "").lower()
        in {"1", "true", "yes", "on"}
    )
    values = {
        "subject": subject,
        "participant_ids": participant_values,
        "admin_accessible": admin_accessible,
    }

    error = _validate_subject(subject) or participant_error
    if not error and _valid_recipient_ids(participant_ids) != set(participant_ids):
        error = "One or more selected participants are unavailable."
    if error:
        return _render_edit(
            conversation,
            values=values,
            is_admin=permissions.can_view_restricted,
            error=error,
            status=400,
        )

    desired_participant_ids = [conversation["owner_account_id"], *participant_ids]

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            """
            UPDATE conversation
            SET subject = %s, admin_accessible = %s
            WHERE id = %s
            """,
            (subject, admin_accessible, conversation_id),
        )
        if not admin_accessible:
            cursor.execute(
                """
                UPDATE conversation_participant
                SET role = 'participant'
                WHERE conversation_id = %s AND role = 'admin'
                """,
                (conversation_id,),
            )
        cursor.execute(
            """
            DELETE FROM conversation_participant
            WHERE conversation_id = %s
              AND NOT (account_id = ANY(%s))
            """,
            (conversation_id, desired_participant_ids),
        )
        cursor.executemany(
            """
            INSERT INTO conversation_participant (
                conversation_id, account_id, role, email_notifications
            )
            VALUES (%s, %s, 'participant', %s)
            ON CONFLICT DO NOTHING
            """,
            [
                (
                    conversation_id,
                    participant_id,
                    conversation["created_by_admin"]
                    or conversation["system_conversation"],
                )
                for participant_id in desired_participant_ids
            ],
        )
        db.commit()
    except psycopg.Error:
        db.rollback()
        current_app.logger.exception("Could not edit conversation %s", conversation_id)
        return _render_edit(
            conversation,
            values=values,
            is_admin=permissions.can_view_restricted,
            error="The conversation could not be updated.",
            status=400,
        )

    return redirect(url_for("messages.thread", conversation_id=conversation_id))


@bp.get("/<int:conversation_id>")
@with_auth
def thread(
    conversation_id: int,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    conversation = _conversation_for_user(conversation_id, user_id, permissions)
    if conversation is None:
        return render_template("error.html", error="Conversation not found"), 404

    page = _positive_page(request.args.get("page"))
    messages, has_older = _message_rows(conversation_id, page, user_id)
    mark_conversation_read(
        conversation_id,
        user_id,
        (message["id"] for message in messages),
    )
    participants = _conversation_participants(conversation_id, user_id)
    can_reply = not conversation["system_conversation"]

    return render_template(
        "messages/thread.html",
        conversation=conversation,
        participants=participants,
        messages=messages,
        can_reply=can_reply,
        can_edit=_can_edit_conversation(conversation, user_id, permissions),
        can_leave=_can_leave_conversation(conversation),
        is_admin=permissions.can_view_restricted,
        page=page,
        has_older=has_older,
        max_message_length=MAX_MESSAGE_LENGTH,
        show_avatars=_show_message_avatars(),
    )


@bp.post("/<int:conversation_id>/notifications")
@with_auth
def notification_preferences(
    conversation_id: int,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    conversation = _conversation_for_user(conversation_id, user_id, permissions)
    if conversation is None:
        return render_template("error.html", error="Conversation not found"), 404
    if not conversation["is_participant"]:
        return (
            render_template(
                "error.html",
                error="Only conversation participants can change notification preferences",
            ),
            403,
        )

    email_notifications = request.form.get("email_notifications", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    suppress_unread_highlight = request.form.get(
        "suppress_unread_highlight", ""
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    pinned = request.form.get("pinned", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        UPDATE conversation_participant
        SET email_notifications = %s,
            suppress_unread_highlight = %s,
            pinned = %s
        WHERE conversation_id = %s AND account_id = %s
        """,
        (
            email_notifications,
            suppress_unread_highlight,
            pinned,
            conversation_id,
            user_id,
        ),
    )
    db.commit()
    return redirect(url_for("messages.thread", conversation_id=conversation_id))


@bp.post("/<int:conversation_id>/leave")
@with_auth
def leave(
    conversation_id: int,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    conversation = _conversation_for_user(conversation_id, user_id, permissions)
    if conversation is None:
        return render_template("error.html", error="Conversation not found"), 404
    if not _can_leave_conversation(conversation):
        return (
            render_template(
                "error.html",
                error="Only invited participants can leave this conversation",
            ),
            403,
        )

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        DELETE FROM conversation_participant
        WHERE conversation_id = %s
          AND account_id = %s
          AND role = 'participant'
        RETURNING account_id
        """,
        (conversation_id, user_id),
    )
    if cursor.fetchone() is None:
        db.rollback()
        return (
            render_template(
                "error.html",
                error="You are no longer an invited participant",
            ),
            403,
        )
    db.commit()
    return redirect(url_for("messages.inbox"))


@bp.post("/<int:conversation_id>/reply")
@with_auth
def reply(
    conversation_id: int,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, _username = user
    conversation = _conversation_for_user(conversation_id, user_id, permissions)
    if conversation is None:
        return render_template("error.html", error="Conversation not found"), 404
    if conversation["system_conversation"]:
        return render_template("error.html", error="System notifications cannot be replied to"), 403

    body = request.form.get("body", "")
    error = _validate_body(body)
    if error:
        messages, has_older = _message_rows(conversation_id, 1, user_id)
        return (
            render_template(
                "messages/thread.html",
                conversation=conversation,
                participants=_conversation_participants(conversation_id, user_id),
                messages=messages,
                can_reply=True,
                can_edit=_can_edit_conversation(conversation, user_id, permissions),
                can_leave=_can_leave_conversation(conversation),
                is_admin=permissions.can_view_restricted,
                error=error,
                reply_body=body,
                page=1,
                has_older=has_older,
                max_message_length=MAX_MESSAGE_LENGTH,
                show_avatars=_show_message_avatars(),
            ),
            400,
        )

    participant_role = conversation["participant_role"]
    acting_as_admin = permissions.can_view_restricted and (
        participant_role == "admin"
        or (participant_role == "owner" and conversation["created_by_admin"])
        or participant_role is None
    )
    sender_kind = "admin" if acting_as_admin else "participant"
    db = get_db()
    cursor = db.cursor()
    try:
        if participant_role is None:
            cursor.execute(
                """
                INSERT INTO conversation_participant (
                    conversation_id, account_id, role, email_notifications
                )
                VALUES (%s, %s, 'admin', %s)
                ON CONFLICT (conversation_id, account_id) DO NOTHING
                """,
                (
                    conversation_id,
                    user_id,
                    conversation["created_by_admin"]
                    or conversation["system_conversation"],
                ),
            )
        elif participant_role == "admin" and not acting_as_admin:
            cursor.execute(
                """
                UPDATE conversation_participant
                SET role = 'participant'
                WHERE conversation_id = %s AND account_id = %s
                """,
                (conversation_id, user_id),
            )
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, user_id, sender_kind, body),
        )
        inserted = cursor.fetchone()
        assert inserted is not None
        message_id = inserted["id"]
        db.commit()
    except psycopg.Error:
        db.rollback()
        current_app.logger.exception("Could not reply to conversation %s", conversation_id)
        return render_template("error.html", error="The reply could not be sent"), 400

    notify_new_message(conversation_id, message_id)
    return redirect(
        url_for("messages.thread", conversation_id=conversation_id, _anchor=f"message-{message_id}")
    )


def _parse_date(value: str, label: str) -> tuple[datetime.date | None, str | None]:
    try:
        return datetime.date.fromisoformat(value), None
    except ValueError:
        return None, f"{label} must be a valid date."


def _messaging_timezone() -> ZoneInfo:
    name = current_app.config.get("MESSAGING_TIMEZONE", "Europe/Warsaw")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        current_app.logger.error("Unknown MESSAGING_TIMEZONE %r; using UTC", name)
        return ZoneInfo("UTC")


def _date_bounds(args) -> tuple[datetime.datetime | None, datetime.datetime | None, str | None]:
    mode = args.get("date_mode", "").strip().lower()
    if not mode:
        return None, None, None
    if mode not in DATE_MODES:
        return None, None, "Invalid date filter."

    timezone = _messaging_timezone()

    def midnight(value: datetime.date) -> datetime.datetime:
        return datetime.datetime.combine(value, datetime.time.min, tzinfo=timezone)

    if mode == "between":
        start, error = _parse_date(args.get("date_from", ""), "Start date")
        if error:
            return None, None, error
        end, error = _parse_date(args.get("date_to", ""), "End date")
        if error:
            return None, None, error
        assert start is not None and end is not None
        if start > end:
            return None, None, "Start date must not be after end date."
        return midnight(start), midnight(end + datetime.timedelta(days=1)), None

    value, error = _parse_date(args.get("date", ""), "Date")
    if error:
        return None, None, error
    assert value is not None
    start = midnight(value)
    next_day = midnight(value + datetime.timedelta(days=1))
    if mode == "exact":
        return start, next_day, None
    if mode == "before":
        return None, start, None
    return next_day, None, None


def _search_filter_accounts(
    user_id: int,
    permissions: UserPermissions,
    *,
    approved_only: bool = False,
) -> list[dict]:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT account.id, account.username
        FROM account
        WHERE account.id <> %s
          AND (%s = false OR account.approved)
          AND (
              EXISTS (
                  SELECT 1
                  FROM conversation_participant mine
                  JOIN conversation_participant theirs
                    ON theirs.conversation_id = mine.conversation_id
                  WHERE mine.account_id = %s
                    AND theirs.account_id = account.id
              )
              OR EXISTS (
                  SELECT 1
                  FROM message
                  JOIN conversation ON conversation.id = message.conversation_id
                  WHERE message.sender_id = account.id
                    AND (
                        EXISTS (
                            SELECT 1
                            FROM conversation_participant mine
                            WHERE mine.conversation_id = conversation.id
                              AND mine.account_id = %s
                        )
                        OR (conversation.admin_accessible AND %s)
                    )
              )
          )
        ORDER BY LOWER(account.username), account.id
        """,
        (
            user_id,
            approved_only,
            user_id,
            user_id,
            permissions.can_view_restricted,
        ),
    )
    return cursor.fetchall()


@bp.get("/search")
@with_auth
def search(user: tuple[int, str] | None, permissions: UserPermissions):
    auth_error = _login_required(user)
    if auth_error:
        return auth_error
    assert user is not None
    user_id, username = user

    query = request.args.get("q", "").strip()
    subject = request.args.get("subject", "").strip()
    sender = request.args.get("sender", "").strip().lower()
    raw_participants = request.args.getlist("participant")
    page = _positive_page(request.args.get("page"))
    error = None

    participant_ids: list[int] = []
    try:
        participant_ids = sorted({int(value) for value in raw_participants if int(value) > 0})
    except ValueError:
        error = "Invalid participant filter."

    sender_id: int | None = None
    system_sender = sender == "system"
    self_sender = sender == "me"
    if self_sender:
        sender_id = user_id
    elif sender and not system_sender:
        try:
            sender_id = int(sender)
            if sender_id <= 0:
                raise ValueError
        except ValueError:
            error = error or "Invalid sender filter."

    lower_bound, upper_bound, date_error = _date_bounds(request.args)
    error = error or date_error

    results: list[dict] = []
    has_next = False
    if not error:
        clauses = [
            """(
                EXISTS (
                    SELECT 1
                    FROM conversation_participant mine
                    WHERE mine.conversation_id = conversation.id
                      AND mine.account_id = %s
                )
                OR (conversation.admin_accessible AND %s)
            )"""
        ]
        params: list = [user_id, permissions.can_view_restricted]

        if query:
            clauses.append(
                """(
                    message.body_search_vector @@ websearch_to_tsquery('simple', %s)
                    OR conversation.subject_search_vector
                       @@ websearch_to_tsquery('simple', %s)
                )"""
            )
            params.extend([query, query])
        if subject:
            clauses.append(
                "conversation.subject_search_vector @@ websearch_to_tsquery('simple', %s)"
            )
            params.append(subject)
        if system_sender:
            clauses.append("message.sender_kind = 'system'")
        elif sender_id is not None:
            clauses.append("message.sender_id = %s")
            params.append(sender_id)
        if lower_bound is not None:
            clauses.append("message.created_at >= %s")
            params.append(lower_bound)
        if upper_bound is not None:
            clauses.append("message.created_at < %s")
            params.append(upper_bound)
        for participant_id in participant_ids:
            clauses.append(
                """EXISTS (
                    SELECT 1
                    FROM conversation_participant selected_participant
                    WHERE selected_participant.conversation_id = conversation.id
                      AND selected_participant.account_id = %s
                )"""
            )
            params.append(participant_id)

        params.extend([SEARCH_PAGE_SIZE + 1, (page - 1) * SEARCH_PAGE_SIZE])
        cursor = get_db().cursor()
        search_sql = typing.cast(
            typing.LiteralString,
            f"""
            SELECT message.id, message.conversation_id, message.sender_id,
                   message.sender_kind, message.body, message.created_at,
                   sender.username AS sender_username,
                   conversation.subject, conversation.admin_accessible,
                   participants.usernames AS participant_usernames
            FROM message
            JOIN conversation ON conversation.id = message.conversation_id
            LEFT JOIN account AS sender ON sender.id = message.sender_id
            LEFT JOIN LATERAL (
                SELECT ARRAY_AGG(account.username ORDER BY LOWER(account.username), account.id)
                       AS usernames
                FROM conversation_participant
                JOIN account ON account.id = conversation_participant.account_id
                WHERE conversation_participant.conversation_id = conversation.id
            ) AS participants ON true
            WHERE {" AND ".join(clauses)}
            ORDER BY message.created_at DESC, message.id DESC
            LIMIT %s OFFSET %s
            """,
        )
        cursor.execute(search_sql, params)
        results = cursor.fetchall()
        has_next = len(results) > SEARCH_PAGE_SIZE
        results = results[:SEARCH_PAGE_SIZE]
        for result in results:
            result["body"] = message_preview(result["body"])

    page_args = request.args.to_dict(flat=False)
    page_args.pop("page", None)

    def search_page_url(target_page: int) -> str:
        target_args = {**page_args, "page": [str(target_page)]}
        return f"{url_for('messages.search')}?{urllib.parse.urlencode(target_args, doseq=True)}"

    previous_url = (
        search_page_url(page - 1) if page > 1 else None
    )
    next_url = search_page_url(page + 1) if has_next else None

    return render_template(
        "messages/search.html",
        results=results,
        sender_filter_accounts=_search_filter_accounts(user_id, permissions),
        participant_filter_accounts=_search_filter_accounts(
            user_id,
            permissions,
            approved_only=True,
        ),
        current_username=username,
        is_admin=permissions.can_view_restricted,
        filters=request.args,
        error=error,
        page=page,
        has_next=has_next,
        previous_url=previous_url,
        next_url=next_url,
        date_modes=DATE_MODES,
    ), (400 if error else 200)
