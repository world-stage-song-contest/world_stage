BEGIN;

ALTER TABLE year ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'
    CHECK (jsonb_typeof(metadata) = 'object');
ALTER TABLE show ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'
    CHECK (jsonb_typeof(metadata) = 'object');

COMMIT;
