BEGIN;

UPDATE country SET genre = NULL;

ALTER TABLE country
    ALTER COLUMN genre TYPE smallint[] USING NULL::smallint[],
    ADD COLUMN IF NOT EXISTS subgenre smallint;

COMMIT;
