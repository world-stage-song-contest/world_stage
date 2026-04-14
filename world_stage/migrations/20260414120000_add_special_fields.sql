BEGIN;

ALTER TABLE year ADD COLUMN special_name text;
ALTER TABLE year ADD COLUMN special_short_name text COLLATE public.nocase;

ALTER TABLE year ADD CONSTRAINT year_special_name_check
    CHECK ((id >= 0 AND special_name IS NULL AND special_short_name IS NULL)
        OR (id < 0 AND special_name IS NOT NULL AND special_short_name IS NOT NULL));

ALTER TABLE year ADD CONSTRAINT year_special_short_name_key
    UNIQUE (special_short_name);

ALTER TABLE song ADD COLUMN entry_number integer NOT NULL DEFAULT 1;

ALTER TABLE song DROP CONSTRAINT song_year_id_country_id_key;
DROP INDEX IF EXISTS idx_25550562_sqlite_autoindex_song_1;
ALTER TABLE song ADD CONSTRAINT song_year_country_entry_key
    UNIQUE (year_id, country_id, entry_number);

COMMIT;
