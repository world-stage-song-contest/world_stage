BEGIN;

ALTER TABLE conversation_participant ADD COLUMN suppress_unread_highlight boolean NOT NULL DEFAULT false;
ALTER TABLE conversation_participant ADD COLUMN pinned boolean NOT NULL DEFAULT false;

COMMIT;