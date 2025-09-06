BEGIN;

ALTER TABLE country
DROP COLUMN bgr_colour,
DROP COLUMN fg1_colour,
DROP COLUMN fg2_colour,
DROP COLUMN txt_colour;

ALTER TABLE vote_set ADD COLUMN ip_address inet;

CREATE OR REPLACE FUNCTION redact_ip_address(p_show_id bigint)
RETURNS void AS $$
BEGIN
    UPDATE vote_set
    SET ip_address = NULL
    WHERE show_id = p_show_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trigger_redact_ip_address()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM redact_ip_address(NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER redact_points_on_voting_close
    AFTER UPDATE OF access_type ON show
    FOR EACH ROW
    WHEN ((NEW.access_type IS DISTINCT FROM 'draw')
          AND (OLD.access_type = 'full'))
    EXECUTE FUNCTION trigger_redact_ip_address();

COMMIT;