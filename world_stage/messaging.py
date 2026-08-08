import re
import smtplib
from collections.abc import Iterable

from flask import current_app, url_for

from .db import get_db
from .email import external_url, is_configured, send_email
from .utils import UserPermissions
from .utils.markdown import strip_font_tags

MESSAGE_TAG_RE = re.compile(
    r"\[/?(?:b|i|u|s|o|sm|xl|pre|code|c(?:=[A-Za-z]+)?|bg(?:=[A-Za-z]+)?)\]",
    re.IGNORECASE,
)


def message_preview(value: str) -> str:
    """Remove supported formatting tags from a plain-text message preview."""
    return strip_font_tags(MESSAGE_TAG_RE.sub("", value))


def notify_new_message(conversation_id: int, message_id: int) -> None:
    """Email opted-in participants after a new message has been committed."""
    if not is_configured():
        return
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT conversation.subject, message.body, message.sender_id,
               COALESCE(sender.username, 'World Stage') AS sender_username
        FROM message
        JOIN conversation ON conversation.id = message.conversation_id
        LEFT JOIN account AS sender ON sender.id = message.sender_id
        WHERE message.id = %s AND message.conversation_id = %s
        """,
        (message_id, conversation_id),
    )
    message = cursor.fetchone()
    if message is None:
        return

    cursor.execute(
        """
        SELECT account.email
        FROM conversation_participant
        JOIN account ON account.id = conversation_participant.account_id
        WHERE conversation_participant.conversation_id = %s
          AND conversation_participant.email_notifications
          AND NULLIF(BTRIM(account.email), '') IS NOT NULL
          AND account.id IS DISTINCT FROM %s
        """,
        (conversation_id, message["sender_id"]),
    )
    recipients = [row["email"].strip() for row in cursor.fetchall()]
    if not recipients:
        return

    try:
        link = external_url(url_for("messages.thread", conversation_id=conversation_id))
    except RuntimeError:
        current_app.logger.exception("Could not build message notification URL")
        return

    subject = f"New message: {message['subject']}"
    body = (
        f"{message['sender_username']} posted a new message in "
        f"\"{message['subject']}\":\n\n{message_preview(message['body'])}\n\n"
        f"Read and reply: {link}\n"
    )
    for recipient in recipients:
        try:
            send_email(recipient, subject, body)
        except (OSError, RuntimeError, ValueError, smtplib.SMTPException):
            current_app.logger.exception(
                "Could not email message notification to %s", recipient
            )


def has_unread_messages(user_id: int, permissions: UserPermissions) -> bool:
    """Return whether the user can access at least one unread message."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM conversation
            JOIN message ON message.conversation_id = conversation.id
            LEFT JOIN conversation_read_state
              ON conversation_read_state.conversation_id = conversation.id
             AND conversation_read_state.account_id = %s
            WHERE (
                EXISTS (
                    SELECT 1
                    FROM conversation_participant
                    WHERE conversation_participant.conversation_id = conversation.id
                      AND conversation_participant.account_id = %s
                )
                OR (conversation.admin_accessible AND %s)
            )
              AND message.id > COALESCE(
                  conversation_read_state.last_read_message_id,
                  0
              )
              AND message.sender_id IS DISTINCT FROM %s
        ) AS has_unread
        """,
        (
            user_id,
            user_id,
            permissions.can_view_restricted,
            user_id,
        ),
    )
    row = cursor.fetchone()
    return bool(row and row["has_unread"])


def has_unread_admin_messages(user_id: int) -> bool:
    """Return whether the shared administrator inbox contains an unread message."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM conversation
            JOIN message ON message.conversation_id = conversation.id
            LEFT JOIN conversation_read_state
              ON conversation_read_state.conversation_id = conversation.id
             AND conversation_read_state.account_id = %s
            WHERE conversation.admin_accessible
              AND message.id > COALESCE(
                  conversation_read_state.last_read_message_id,
                  0
              )
              AND message.sender_id IS DISTINCT FROM %s
        ) AS has_unread
        """,
        (user_id, user_id),
    )
    row = cursor.fetchone()
    return bool(row and row["has_unread"])


def banner_conversations(user_id: int) -> list[dict]:
    """Return metadata-designated conversations visible as home-page banners."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT conversation.id, conversation.subject
        FROM conversation
        JOIN conversation_participant
          ON conversation_participant.conversation_id = conversation.id
         AND conversation_participant.account_id = %s
        WHERE conversation.metadata @> jsonb_build_object(
            'banner', true,
            'submitter_id', %s::bigint
        )
        ORDER BY conversation.created_at DESC, conversation.id DESC
        """,
        (user_id, user_id),
    )
    return cursor.fetchall()


def mark_conversation_read(
    conversation_id: int,
    user_id: int,
    message_ids: Iterable[int],
) -> None:
    """Advance a user's read position through the displayed messages."""
    last_read_message_id = max(message_ids, default=0)
    if not last_read_message_id:
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO conversation_read_state (
            conversation_id, account_id, last_read_message_id
        )
        VALUES (%s, %s, %s)
        ON CONFLICT (conversation_id, account_id) DO UPDATE
        SET last_read_message_id = GREATEST(
                conversation_read_state.last_read_message_id,
                EXCLUDED.last_read_message_id
            ),
            updated_at = CURRENT_TIMESTAMP
        """,
        (conversation_id, user_id, last_read_message_id),
    )
    db.commit()
