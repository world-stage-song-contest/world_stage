BEGIN;

-- Free-text annotation per key-signature row, e.g. for noting that
-- the key is approximate, drifts, or doesn't fit the standard 12-tone
-- spelling exactly.

ALTER TABLE song_key_signature
    ADD COLUMN IF NOT EXISTS notes text;

COMMIT;
