ALTER TABLE song_show ADD COLUMN IF NOT EXISTS revote_penalty integer NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION refresh_revote_penalties(p_show_id bigint)
RETURNS void AS $$
DECLARE
    max_point integer;
BEGIN
    SELECT MAX(point.score) INTO max_point
    FROM show JOIN point ON point.point_system_id = show.point_system_id
    WHERE show.id = p_show_id;

    UPDATE song_show ss
    SET revote_penalty = CASE
        WHEN song.submitter_id IS NULL THEN 0
        WHEN EXISTS (
            SELECT 1 FROM vote_set
            WHERE show_id = p_show_id AND voter_id = song.submitter_id
              AND result_mode = 'official'
        ) THEN 0
        WHEN EXISTS (
            SELECT 1 FROM vote_set
            WHERE show_id = p_show_id AND voter_id = song.submitter_id
              AND result_mode = 'revote'
        ) THEN 0
        ELSE COALESCE(max_point, 0)
    END
    FROM song
    WHERE ss.show_id = p_show_id AND song.id = ss.song_id;
END;
$$ LANGUAGE plpgsql;

-- Apply the Revote-specific penalty without changing official results.
DO $$
DECLARE definition text;
BEGIN
    SELECT pg_get_functiondef('refresh_show_results_for_mode(bigint,text)'::regprocedure)
    INTO definition;
    IF position('ss.revote_penalty' IN definition) = 0 THEN
        definition := replace(
            definition,
            'COALESCE(ss.penalty, 0)',
            'COALESCE(CASE WHEN p_result_mode = ''revote'' THEN ss.revote_penalty ELSE ss.penalty END, 0)'
        );
        definition := replace(
            definition,
            'ss.running_order, ss.penalty,',
            'ss.running_order, ss.penalty, ss.revote_penalty,'
        );
        EXECUTE definition;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trigger_refresh_show_results_from_vote_set()
RETURNS TRIGGER AS $$
DECLARE
    v_show_id bigint;
    v_result_mode text;
BEGIN
    v_show_id := COALESCE(NEW.show_id, OLD.show_id);
    v_result_mode := COALESCE(NEW.result_mode, OLD.result_mode);
    IF v_show_id IS NOT NULL THEN
        IF v_result_mode = 'revote' THEN
            PERFORM refresh_revote_penalties(v_show_id);
        END IF;
        PERFORM refresh_show_results_for_mode(v_show_id, v_result_mode);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION initialize_revote_penalties_for_show()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.revote_eligible_at IS NOT NULL THEN
        PERFORM refresh_revote_penalties(NEW.id);
        IF EXISTS (SELECT 1 FROM vote_set WHERE show_id = NEW.id AND result_mode = 'revote') THEN
            PERFORM refresh_show_results_for_mode(NEW.id, 'revote');
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_initialize_revote_penalties ON show;
CREATE TRIGGER trg_initialize_revote_penalties
AFTER INSERT OR UPDATE OF revote_eligible_at ON show
FOR EACH ROW EXECUTE FUNCTION initialize_revote_penalties_for_show();

DO $$
DECLARE show_row record;
BEGIN
    FOR show_row IN SELECT id FROM show WHERE revote_eligible_at IS NOT NULL LOOP
        PERFORM refresh_revote_penalties(show_row.id);
        IF EXISTS (SELECT 1 FROM vote_set WHERE show_id = show_row.id AND result_mode = 'revote') THEN
            PERFORM refresh_show_results_for_mode(show_row.id, 'revote');
        END IF;
    END LOOP;
END;
$$;
