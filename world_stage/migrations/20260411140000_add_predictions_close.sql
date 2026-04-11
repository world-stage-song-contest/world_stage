BEGIN;

ALTER TABLE show ADD COLUMN predictions_close timestamptz;

COMMIT;
