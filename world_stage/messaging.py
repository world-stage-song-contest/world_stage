import re
from collections.abc import Iterable

from .db import get_db
from .utils import UserPermissions

MESSAGE_TAG_RE = re.compile(
    r"\[/?(?:b|i|u|s|o|sm|xl|pre|code|c(?:=[A-Za-z]+)?|bg(?:=[A-Za-z]+)?)\]",
    re.IGNORECASE,
)


def message_preview(value: str) -> str:
    """Remove supported formatting tags from a plain-text message preview."""
    return MESSAGE_TAG_RE.sub("", value)


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
