-- Earlier Revote installations used the historical same-country exclusion
-- when refreshing Revote caches.  Revotes always follow the current rule:
-- a vote counts regardless of the voter's displayed country.
DO $$
DECLARE
    definition text;
    old_filter constant text := E'        WHERE\n            (si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979)';
    new_filter constant text := E'        WHERE\n            p_result_mode = ''revote''\n            OR (si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979)';
BEGIN
    SELECT pg_get_functiondef('refresh_show_results_for_mode(bigint,text)'::regprocedure)
    INTO definition;

    -- Fresh installs already have the corrected definition in the preceding
    -- migration. Existing installs receive the replacement here.
    IF position(E'p_result_mode = ''revote''\n            OR (si.year_id' IN definition) = 0 THEN
        definition := replace(definition, old_filter, new_filter);
        IF position(new_filter IN definition) = 0 THEN
            RAISE EXCEPTION 'Could not update refresh_show_results_for_mode for Revotes';
        END IF;
        EXECUTE definition;
    END IF;
END;
$$;

-- Rebuild already-cached Revote results using the corrected rule.
DO $$
DECLARE
    show_row record;
BEGIN
    FOR show_row IN
        SELECT DISTINCT show_id FROM vote_set WHERE result_mode = 'revote'
    LOOP
        PERFORM refresh_show_results_for_mode(show_row.show_id, 'revote');
    END LOOP;
END;
$$;
