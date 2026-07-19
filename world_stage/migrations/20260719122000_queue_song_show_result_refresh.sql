-- Keep materialized results authoritative when show membership or display data
-- changes, including shows that do not have any ballots yet.

CREATE OR REPLACE FUNCTION trigger_queue_show_result_refresh_from_song_show()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        -- Derived rows have restrictive foreign keys. Remove a row that no
        -- longer belongs to this show immediately so the parent show/song can
        -- be deleted in the same transaction.
        IF TG_OP = 'DELETE'
           OR NEW.song_id IS DISTINCT FROM OLD.song_id
           OR NEW.show_id IS DISTINCT FROM OLD.show_id THEN
            DELETE FROM country_show_results
            WHERE song_id = OLD.song_id AND show_id = OLD.show_id;
        END IF;
        PERFORM queue_show_result_refresh(OLD.show_id, 'official');
    END IF;

    IF TG_OP <> 'DELETE'
       AND (TG_OP <> 'UPDATE' OR NEW.show_id IS DISTINCT FROM OLD.show_id) THEN
        PERFORM queue_show_result_refresh(NEW.show_id, 'official');
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS song_show_penalty_refresh ON song_show;

CREATE TRIGGER trg_queue_show_result_refresh_on_song_show
    AFTER INSERT OR DELETE OR UPDATE OF song_id, show_id, running_order, penalty ON song_show
    FOR EACH ROW
    EXECUTE FUNCTION trigger_queue_show_result_refresh_from_song_show();

DROP FUNCTION IF EXISTS trigger_refresh_show_results_from_song_show();
