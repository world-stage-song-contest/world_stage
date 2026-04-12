-- Switch country primary key from 3-letter (id) to 2-letter (cc2) codes.
-- The old 3-letter code moves to a new cc3 column.
--
-- IMPORTANT: After running this migration, rename the flag directories:
--   psql -c "SELECT 'mv ' || cc3 || ' ' || id FROM country" | sh
-- (run from world_stage/files/flags/)

BEGIN;

-- 1. Add cc3 column and populate from current id (3-letter code)
ALTER TABLE country ADD COLUMN cc3 text COLLATE public.nocase;
UPDATE country SET cc3 = id;
ALTER TABLE country ALTER COLUMN cc3 SET NOT NULL;

-- 2. Drop ALL foreign key constraints referencing country(id)
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tc.table_name, tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
            ON tc.constraint_name = ccu.constraint_name
            AND tc.table_schema = ccu.table_schema
        WHERE ccu.table_name = 'country'
          AND ccu.column_name = 'id'
          AND tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
    ) LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', r.table_name, r.constraint_name);
    END LOOP;
END $$;

-- 3. Drop unique constraints involving country_id on song
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.table_name = 'song'
          AND tc.constraint_type = 'UNIQUE'
          AND kcu.column_name = 'country_id'
          AND tc.table_schema = 'public'
    ) LOOP
        EXECUTE format('ALTER TABLE song DROP CONSTRAINT %I', r.constraint_name);
    END LOOP;
END $$;

-- 4. Drop indexes that reference country columns
DROP INDEX IF EXISTS idx_song_country;
DROP INDEX IF EXISTS idx_song_id_country_id;
DROP INDEX IF EXISTS idx_country_alpha2;
DROP INDEX IF EXISTS idx_csr_country_id;

-- 5. Update all foreign key columns: 3-letter → 2-letter
UPDATE alternative_name an
SET country_id = c.cc2
FROM country c
WHERE an.country_id = c.id;

-- Rename host → host_id while we're touching this table.
ALTER TABLE year RENAME COLUMN host TO host_id;

UPDATE year y
SET host_id = c.cc2
FROM country c
WHERE y.host_id = c.id;

UPDATE song s
SET country_id = c.cc2
FROM country c
WHERE s.country_id = c.id;

UPDATE vote_set vs
SET country_id = c.cc2
FROM country c
WHERE vs.country_id = c.id;

UPDATE country_show_results csr
SET country_id = c.cc2
FROM country c
WHERE csr.country_id = c.id;

UPDATE country_year_results cyr
SET country_id = c.cc2
FROM country c
WHERE cyr.country_id = c.id;

UPDATE song_audit_log sal
SET song_country_id = c.cc2
FROM country c
WHERE sal.song_country_id = c.id;

-- 6. Update country.id from 3-letter to 2-letter (cc2)
--    Must drop PK first since we're changing PK values
DO $$
DECLARE
    pk_name text;
BEGIN
    SELECT constraint_name INTO pk_name
    FROM information_schema.table_constraints
    WHERE table_name = 'country'
      AND constraint_type = 'PRIMARY KEY'
      AND table_schema = 'public';
    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE country DROP CONSTRAINT %I', pk_name);
    END IF;
END $$;
UPDATE country SET id = cc2;
ALTER TABLE country ADD PRIMARY KEY (id);

-- 7. Drop the now-redundant cc2 column
ALTER TABLE country DROP COLUMN cc2;

-- 8. Ensure FK columns use the same collation as country.id
ALTER TABLE alternative_name ALTER COLUMN country_id TYPE text COLLATE public.nocase;
ALTER TABLE year ALTER COLUMN host_id TYPE text COLLATE public.nocase;
ALTER TABLE song ALTER COLUMN country_id TYPE text COLLATE public.nocase;
ALTER TABLE vote_set ALTER COLUMN country_id TYPE text COLLATE public.nocase;

-- 9. Remove orphaned alternative_name rows (e.g. SCG not in country table)
DELETE FROM alternative_name
WHERE country_id NOT IN (SELECT id FROM country);

-- 10. Recreate foreign key constraints
ALTER TABLE alternative_name
    ADD CONSTRAINT alternative_name_country_id_fkey
    FOREIGN KEY (country_id) REFERENCES country (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

ALTER TABLE year
    ADD CONSTRAINT year_host_id_fkey
    FOREIGN KEY (host_id) REFERENCES country (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

ALTER TABLE song
    ADD CONSTRAINT song_country_id_fkey
    FOREIGN KEY (country_id) REFERENCES country (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

ALTER TABLE vote_set
    ADD CONSTRAINT vote_set_country_id_fkey
    FOREIGN KEY (country_id) REFERENCES country (id)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

-- Recreate song unique constraint
ALTER TABLE song ADD CONSTRAINT song_year_id_country_id_key
    UNIQUE (year_id, country_id);

-- 9. Recreate indexes
CREATE INDEX idx_song_country ON song (country_id);
CREATE INDEX idx_song_id_country_id ON song (id, country_id);
CREATE INDEX idx_country_cc3 ON country (cc3);

-- 10. Update refresh_show_results to use new column names
--     (the function body references country.id which is now cc2-based — this is fine,
--      but country_name still comes from country.name which hasn't changed)

COMMIT;
