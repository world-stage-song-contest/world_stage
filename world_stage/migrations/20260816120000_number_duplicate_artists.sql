BEGIN;

ALTER TABLE artist_credit ALTER COLUMN stage_name DROP NOT NULL;
ALTER TABLE artist ADD COLUMN number integer;

-- Existing rows that differ only by case or native-script name were allowed by
-- the original artist migration. Give each such identity a stable number before
-- enforcing the new full-name/number uniqueness rule.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY LOWER(full_name)
               ORDER BY id
           )::integer AS number
    FROM artist
)
UPDATE artist
SET number = ranked.number
FROM ranked
WHERE artist.id = ranked.id;

ALTER TABLE artist
    ALTER COLUMN number SET DEFAULT 1,
    ALTER COLUMN number SET NOT NULL,
    ADD CONSTRAINT artist_number_positive CHECK (number > 0);

DROP INDEX idx_artist_names_ci;

CREATE UNIQUE INDEX idx_artist_name_number_ci
    ON artist (LOWER(full_name), number);

COMMIT;