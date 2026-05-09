BEGIN;

-- Free-text annotation per time-signature row, e.g. for noting the
-- specific groupings of a mixed meter or any non-standard subdivision.

ALTER TABLE song_time_signature
    ADD COLUMN IF NOT EXISTS notes text;

COMMIT;
