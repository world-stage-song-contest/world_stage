from flask import request

from ...db import get_db
from ...messaging import message_preview
from ...utils import UserPermissions, render_template, with_auth
from .common import bp

ADMIN_INBOX_PAGE_SIZE = 50


def _positive_page(raw: str | None) -> int:
    try:
        return max(1, int(raw or "1"))
    except ValueError:
        return 1


@bp.get("/messages")
@with_auth
def messages_inbox(
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    assert user is not None and permissions.can_view_restricted
    user_id, _username = user
    page = _positive_page(request.args.get("page"))

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT conversation.id, conversation.subject,
               conversation.created_by_admin, conversation.system_conversation,
               conversation.created_at,
               participants.usernames AS participant_usernames,
               latest.body AS latest_message_body,
               latest.created_at AS latest_message_at,
               latest.sender_kind AS latest_sender_kind,
               latest.sender_username AS latest_sender_username,
               unread.unread_count,
               current_participant.role AS current_participant_role,
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
            SELECT ARRAY_AGG(
                       account.username
                       ORDER BY LOWER(account.username), account.id
                   ) AS usernames
            FROM conversation_participant
            JOIN account ON account.id = conversation_participant.account_id
            WHERE conversation_participant.conversation_id = conversation.id
        ) AS participants ON true
        LEFT JOIN LATERAL (
            SELECT message.body, message.created_at, message.sender_kind,
                   account.username AS sender_username
            FROM message
            LEFT JOIN account ON account.id = message.sender_id
            WHERE message.conversation_id = conversation.id
            ORDER BY message.created_at DESC, message.id DESC
            LIMIT 1
        ) AS latest ON true
        WHERE conversation.admin_accessible
        ORDER BY COALESCE(current_participant.pinned, false) DESC,
                 COALESCE(latest.created_at, conversation.created_at) DESC,
                 conversation.id DESC
        LIMIT %s OFFSET %s
        """,
        (
            user_id,
            user_id,
            user_id,
            ADMIN_INBOX_PAGE_SIZE + 1,
            (page - 1) * ADMIN_INBOX_PAGE_SIZE,
        ),
    )
    conversations = cursor.fetchall()
    has_next = len(conversations) > ADMIN_INBOX_PAGE_SIZE
    conversations = conversations[:ADMIN_INBOX_PAGE_SIZE]
    for conversation in conversations:
        if conversation["latest_message_body"]:
            conversation["latest_message_body"] = message_preview(
                conversation["latest_message_body"]
            )

    return render_template(
        "admin/messages.html",
        conversations=conversations,
        page=page,
        has_next=has_next,
    )
