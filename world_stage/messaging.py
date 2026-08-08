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
        SELECT conversation.subject, conversation.system_conversation,
               message.body, message.sender_id,
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

    if message["system_conversation"]:
        subject = f"Notification: {message['subject']}"
        body = (
            f"{message_preview(message['body'])}\n\n"
            f"View notification: {link}\n"
        )
    else:
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


def create_spot_watch_notifications(
    cursor,
    song_id: int,
    event: str,
) -> list[tuple[int, int]]:
    """Create one private system notification for each watcher of a song's spot.

    The caller owns the transaction and should call ``notify_new_message`` for
    each returned pair only after committing it.
    """
    if event not in {"deleted", "placeholder"}:
        raise ValueError(f"Unknown watched-spot event: {event}")

    cursor.execute(
        """
        SELECT stable.year_id, stable.country_id,
               COALESCE(stable.entry_number, 1) AS entry_number,
               country.name AS country_name,
               latest.title, latest.artist
        FROM song AS stable
        JOIN country ON country.id = stable.country_id
        LEFT JOIN LATERAL (
            SELECT data.title, data.artist
            FROM song_data AS data
            WHERE data.song_id = stable.id
              AND data.title IS NOT NULL
              AND data.artist IS NOT NULL
            ORDER BY data.created_at DESC, data.id DESC
            LIMIT 1
        ) AS latest ON true
        WHERE stable.id = %s
        """,
        (song_id,),
    )
    spot = cursor.fetchone()
    if spot is None:
        return []

    cursor.execute(
        """
        SELECT account_id
        FROM year_spot_watch
        WHERE year_id = %s
          AND country_id = %s
          AND entry_number = %s
        ORDER BY account_id
        """,
        (spot["year_id"], spot["country_id"], spot["entry_number"]),
    )
    watcher_ids = [row["account_id"] for row in cursor.fetchall()]
    if not watcher_ids:
        return []

    song_label = (
        f"{spot['artist']} – {spot['title']}"
        if spot["artist"] and spot["title"]
        else "The song"
    )
    action = "was deleted" if event == "deleted" else "became a placeholder"
    subject = f"Watched spot changed: {spot['country_name']} in {spot['year_id']}"[:200]
    body = (
        f"{song_label} {action} in the {spot['country_name']} spot for "
        f"{spot['year_id']}. You are receiving this because you watch this spot."
    )

    notifications = []
    for watcher_id in watcher_ids:
        cursor.execute(
            """
            INSERT INTO conversation (
                subject, metadata, system_conversation
            ) VALUES (
                %s,
                jsonb_build_object(
                    'year_id', %s::bigint,
                    'country_id', %s::text,
                    'entry_number', %s::integer,
                    'spot_watch_event', %s::text
                ),
                true
            )
            RETURNING id
            """,
            (
                subject,
                spot["year_id"],
                spot["country_id"],
                spot["entry_number"],
                event,
            ),
        )
        conversation_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO conversation_participant (
                conversation_id, account_id, role, email_notifications
            ) VALUES (%s, %s, 'participant', true)
            """,
            (conversation_id, watcher_id),
        )
        cursor.execute(
            """
            INSERT INTO message (conversation_id, sender_id, sender_kind, body)
            VALUES (%s, NULL, 'system', %s)
            RETURNING id
            """,
            (conversation_id, body),
        )
        notifications.append((conversation_id, cursor.fetchone()["id"]))
    return notifications


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
