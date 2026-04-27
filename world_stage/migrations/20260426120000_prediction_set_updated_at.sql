BEGIN;

ALTER TABLE prediction_set
    ADD COLUMN IF NOT EXISTS updated_at timestamptz;

COMMIT;
