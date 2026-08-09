CREATE OR REPLACE FUNCTION validate_admin_participant_role()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.role = 'admin' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM conversation
            WHERE id = NEW.conversation_id AND admin_accessible
        ) THEN
            RAISE EXCEPTION 'admin participants require shared admin access';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM account
            JOIN account_role ON account_role.name = account.role
            WHERE account.id = NEW.account_id
              AND (account.role = 'editor' OR account_role.can_view_restricted)
        ) THEN
            RAISE EXCEPTION 'admin participant does not have moderator access';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_message_sender()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    target_conversation conversation%ROWTYPE;
BEGIN
    SELECT *
    INTO target_conversation
    FROM conversation
    WHERE id = NEW.conversation_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.sender_kind = 'system' THEN
        IF NOT target_conversation.system_conversation THEN
            RAISE EXCEPTION 'system messages require a system conversation';
        END IF;
    ELSIF NEW.sender_kind = 'participant' THEN
        IF target_conversation.system_conversation THEN
            RAISE EXCEPTION 'system conversations do not accept replies';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM conversation_participant
            WHERE conversation_id = NEW.conversation_id
              AND account_id = NEW.sender_id
              AND role IN ('owner', 'participant')
        ) THEN
            RAISE EXCEPTION 'sender is not an owner or ordinary participant';
        END IF;
    ELSIF NEW.sender_kind = 'admin' THEN
        IF target_conversation.system_conversation THEN
            RAISE EXCEPTION 'system conversations do not accept replies';
        END IF;
        IF NOT target_conversation.admin_accessible THEN
            RAISE EXCEPTION 'conversation does not permit shared admin access';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM account
            JOIN account_role ON account_role.name = account.role
            WHERE account.id = NEW.sender_id
              AND (account.role = 'editor' OR account_role.can_view_restricted)
        ) THEN
            RAISE EXCEPTION 'admin sender does not have moderator access';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM conversation_participant
            WHERE conversation_id = NEW.conversation_id
              AND account_id = NEW.sender_id
              AND (
                  role = 'admin'
                  OR (role = 'owner' AND target_conversation.created_by_admin)
              )
        ) THEN
            RAISE EXCEPTION 'admin sender is not an admin participant';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
