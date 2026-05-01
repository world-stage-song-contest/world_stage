BEGIN;

-- Per-country genre tag, set alongside the pot via the admin "Set Pots"
-- screen. Nullable, with the same convention as ``pot``: 0 in the form
-- maps to NULL in the DB.

ALTER TABLE country ADD COLUMN IF NOT EXISTS genre smallint;

COMMIT;
