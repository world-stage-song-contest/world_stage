BEGIN;

ALTER TABLE account ALTER COLUMN approved DROP DEFAULT;
ALTER TABLE account ALTER COLUMN approved TYPE boolean USING (approved <> 0);
ALTER TABLE account ALTER COLUMN approved SET DEFAULT false;

ALTER TABLE country ALTER COLUMN pot TYPE smallint;
ALTER TABLE country ALTER COLUMN available_from TYPE smallint;
ALTER TABLE country ALTER COLUMN available_until TYPE smallint;

-- year.closed is a tri-state enum (0=open, 1=closed, 2=ongoing). Convert to
-- text and rename to year.status so the meaning lives in the data. Must
-- redefine compute_country_year_results() and its trigger since both still
-- reference the old column name and integer literals.
--
-- The trigger must be dropped BEFORE the ALTER COLUMN TYPE: Postgres refuses
-- to change the type of a column that a trigger definition depends on
-- (UPDATE OF closed / WHEN (NEW.closed = 1)).
DROP TRIGGER IF EXISTS trg_compute_country_year_results ON year;

ALTER TABLE year ALTER COLUMN closed TYPE text USING (
    CASE closed
        WHEN 0 THEN 'open'
        WHEN 1 THEN 'closed'
        WHEN 2 THEN 'ongoing'
    END
);
ALTER TABLE year RENAME COLUMN closed TO status;

CREATE OR REPLACE FUNCTION compute_country_year_results()
RETURNS trigger AS $func$
BEGIN
  IF NEW.status = 'closed' AND OLD.status IS DISTINCT FROM 'closed' THEN
    PERFORM refresh_year_results(NEW.id);
  END IF;
  RETURN NEW;
END;
$func$ LANGUAGE plpgsql;

CREATE TRIGGER trg_compute_country_year_results
AFTER UPDATE OF status ON year
FOR EACH ROW
WHEN (NEW.status = 'closed' AND OLD.status IS DISTINCT FROM 'closed')
EXECUTE FUNCTION compute_country_year_results();

ALTER TABLE song ALTER COLUMN snippet_start TYPE integer;
ALTER TABLE song ALTER COLUMN snippet_end TYPE integer;

ALTER TABLE song_language ALTER COLUMN priority TYPE smallint;

ALTER TABLE point_system ALTER COLUMN number TYPE smallint;
ALTER TABLE point ALTER COLUMN place TYPE smallint;

ALTER TABLE show ALTER COLUMN dtf TYPE smallint;
ALTER TABLE show ALTER COLUMN sc TYPE smallint;
ALTER TABLE show ALTER COLUMN special TYPE smallint;

ALTER TABLE song_show ALTER COLUMN running_order TYPE smallint;
ALTER TABLE song_show ALTER COLUMN qualifier_order TYPE smallint;

COMMIT;