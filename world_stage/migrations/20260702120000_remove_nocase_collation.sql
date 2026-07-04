-- Remove the custom public.nocase collation, replacing collation-backed
-- case-insensitive uniqueness with functional unique indexes on LOWER(...).

BEGIN;

ALTER TABLE account ALTER COLUMN username TYPE text;

DO $$
DECLARE
    c_name text;
BEGIN
    SELECT tc.constraint_name INTO c_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.table_schema = kcu.table_schema
    WHERE tc.table_name = 'account'
      AND tc.constraint_type = 'UNIQUE'
      AND kcu.column_name = 'username'
      AND tc.table_schema = 'public';
    IF c_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE account DROP CONSTRAINT %I', c_name);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS account_username_lower_key ON account (LOWER(username));

-- FK constraints referencing country(id) must be dropped before either side's
-- collation changes: Postgres validates FK collation compatibility as soon as
-- one column's collation diverges from the other's, even mid-transaction.
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

ALTER TABLE country ALTER COLUMN id TYPE text;
ALTER TABLE country ALTER COLUMN name TYPE text;
ALTER TABLE country ALTER COLUMN cc3 TYPE text;

ALTER TABLE year ALTER COLUMN host_id TYPE text;
ALTER TABLE year ALTER COLUMN special_short_name TYPE text;

DO $$
DECLARE
    c_name text;
BEGIN
    SELECT tc.constraint_name INTO c_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.table_schema = kcu.table_schema
    WHERE tc.table_name = 'year'
      AND tc.constraint_type = 'UNIQUE'
      AND kcu.column_name = 'special_short_name'
      AND tc.table_schema = 'public';
    IF c_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE year DROP CONSTRAINT %I', c_name);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS year_special_short_name_lower_key ON year (LOWER(special_short_name));

ALTER TABLE alternative_name ALTER COLUMN country_id TYPE text;
ALTER TABLE song ALTER COLUMN country_id TYPE text;
ALTER TABLE show ALTER COLUMN show_name TYPE text;
ALTER TABLE show ALTER COLUMN short_name TYPE text;
ALTER TABLE vote_set ALTER COLUMN country_id TYPE text;

-- Recreate the FK constraints dropped above (default names match the
-- inline REFERENCES clauses in schema.sql / earlier migrations).
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

DROP COLLATION IF EXISTS public.nocase;

COMMIT;
