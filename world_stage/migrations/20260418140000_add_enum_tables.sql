BEGIN;

-- Lookup tables for previously-ad-hoc text enums. FK references enforce that
-- inserted values are always one of the allowed names.

CREATE TABLE IF NOT EXISTS year_status (name text PRIMARY KEY);
INSERT INTO year_status (name) VALUES ('open'), ('closed'), ('ongoing')
    ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS show_status (name text PRIMARY KEY);
INSERT INTO show_status (name) VALUES ('none'), ('draw'), ('partial'), ('full')
    ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS account_role (name text PRIMARY KEY);
INSERT INTO account_role (name) VALUES ('user'), ('editor'), ('admin'), ('owner')
    ON CONFLICT DO NOTHING;

-- Rename show.access_type to show.status for consistency with year.status.
-- The redact_points_on_voting_close trigger references the column in its
-- WHEN clause; Postgres tracks column references by attnum so the rename is
-- transparent, but recreating it keeps the DDL using the new name.
--
-- While recreating, also fix the inverted WHEN clause: the original fired
-- when status moved AWAY from 'full', which meant IPs were never redacted
-- when a show was actually published. Correct semantics: redact on the
-- transition INTO 'full'.
ALTER TABLE show RENAME COLUMN access_type TO status;

DROP TRIGGER IF EXISTS redact_points_on_voting_close ON show;
CREATE TRIGGER redact_points_on_voting_close
    AFTER UPDATE OF status ON show
    FOR EACH ROW
    WHEN (NEW.status = 'full' AND OLD.status IS DISTINCT FROM 'full')
    EXECUTE FUNCTION trigger_redact_ip_address();

-- Backfill: redact IPs for shows that were already published under the
-- broken trigger so the data matches the intended invariant.
UPDATE vote_set
SET ip_address = NULL
WHERE ip_address IS NOT NULL
  AND show_id IN (SELECT id FROM show WHERE status = 'full');

ALTER TABLE year ADD CONSTRAINT year_status_fkey
    FOREIGN KEY (status) REFERENCES year_status(name)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

ALTER TABLE show ADD CONSTRAINT show_status_fkey
    FOREIGN KEY (status) REFERENCES show_status(name)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

ALTER TABLE account ADD CONSTRAINT account_role_fkey
    FOREIGN KEY (role) REFERENCES account_role(name)
    ON UPDATE RESTRICT ON DELETE RESTRICT;

COMMIT;
