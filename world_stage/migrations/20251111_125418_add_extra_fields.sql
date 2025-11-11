BEGIN;

ALTER TABLE song ADD COLUMN poster_link text;

ALTER TABLE language ADD COLUMN code3 text;

COMMIT;