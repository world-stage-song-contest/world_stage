-- Rebuild materialized show results after a complete ballot transaction instead
-- of once for every row changed in vote and vote_set.

-- The primary key both deduplicates row-trigger requests within a transaction
-- and serializes concurrent ballot refreshes for the same show and mode.
CREATE TABLE show_result_refresh_queue (
    show_id bigint NOT NULL,
    result_mode text NOT NULL
        CHECK (result_mode IN ('official', 'revote')),
    PRIMARY KEY (show_id, result_mode)
);

CREATE OR REPLACE FUNCTION queue_show_result_refresh(
    p_show_id bigint, p_result_mode text
)
RETURNS void AS $$
BEGIN
    IF p_show_id IS NULL OR p_result_mode IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO show_result_refresh_queue (show_id, result_mode)
    VALUES (p_show_id, p_result_mode)
    ON CONFLICT DO NOTHING;

    -- Revote results use official ballots until a voter replaces their ballot.
    -- Keep that view current when an official ballot changes after revoting began.
    IF p_result_mode = 'official' AND EXISTS (
        SELECT 1
        FROM vote_set
        WHERE show_id = p_show_id AND result_mode = 'revote'
    ) THEN
        INSERT INTO show_result_refresh_queue (show_id, result_mode)
        VALUES (p_show_id, 'revote')
        ON CONFLICT DO NOTHING;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION queue_show_result_refresh_from_vote_set_id(
    p_vote_set_id bigint
)
RETURNS void AS $$
DECLARE
    v_show_id bigint;
    v_result_mode text;
BEGIN
    SELECT show_id, result_mode
    INTO v_show_id, v_result_mode
    FROM vote_set
    WHERE id = p_vote_set_id;

    IF FOUND THEN
        PERFORM queue_show_result_refresh(v_show_id, v_result_mode);
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trigger_queue_show_result_refresh_from_vote()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM queue_show_result_refresh_from_vote_set_id(OLD.vote_set_id);
    END IF;

    IF TG_OP <> 'DELETE'
       AND (TG_OP <> 'UPDATE' OR NEW.vote_set_id IS DISTINCT FROM OLD.vote_set_id) THEN
        PERFORM queue_show_result_refresh_from_vote_set_id(NEW.vote_set_id);
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trigger_queue_show_result_refresh_from_vote_set()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM queue_show_result_refresh(OLD.show_id, OLD.result_mode);
    END IF;

    IF TG_OP <> 'DELETE'
       AND (
           TG_OP <> 'UPDATE'
           OR NEW.show_id IS DISTINCT FROM OLD.show_id
           OR NEW.result_mode IS DISTINCT FROM OLD.result_mode
       ) THEN
        PERFORM queue_show_result_refresh(NEW.show_id, NEW.result_mode);
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION process_show_result_refresh_queue()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.result_mode = 'revote' THEN
        PERFORM refresh_revote_penalties(NEW.show_id);
    END IF;

    PERFORM refresh_show_results_for_mode(NEW.show_id, NEW.result_mode);

    DELETE FROM show_result_refresh_queue
    WHERE show_id = NEW.show_id AND result_mode = NEW.result_mode;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_refresh_show_results_on_vote ON vote;
DROP TRIGGER IF EXISTS trg_refresh_show_results_on_vote_set ON vote_set;

CREATE TRIGGER trg_queue_show_result_refresh_on_vote
    AFTER INSERT OR UPDATE OR DELETE ON vote
    FOR EACH ROW
    EXECUTE FUNCTION trigger_queue_show_result_refresh_from_vote();

CREATE TRIGGER trg_queue_show_result_refresh_on_vote_set
    AFTER INSERT OR UPDATE OR DELETE ON vote_set
    FOR EACH ROW
    EXECUTE FUNCTION trigger_queue_show_result_refresh_from_vote_set();

CREATE CONSTRAINT TRIGGER trg_process_show_result_refresh_queue
    AFTER INSERT ON show_result_refresh_queue
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION process_show_result_refresh_queue();

DROP FUNCTION IF EXISTS trigger_refresh_show_results_from_vote();
DROP FUNCTION IF EXISTS trigger_refresh_show_results_from_vote_set();
