-- Apply the full tiebreak to persisted show results:
-- points, number of voting jurors, high-score countback, then running order.
DO $$
DECLARE
    definition text;
    updated_definition text;
BEGIN
    SELECT pg_get_functiondef('refresh_show_results_for_mode(bigint,text)'::regprocedure)
    INTO definition;

    updated_definition := regexp_replace(
        definition,
        'DENSE_RANK\(\) OVER \([[:space:]]*ORDER BY[[:space:]]*total_points DESC,[[:space:]]*total_votes_received DESC,[[:space:]]*countback_string DESC[[:space:]]*\) AS place',
        'ROW_NUMBER() OVER (
                ORDER BY
                    total_points DESC,
                    total_votes_received DESC,
                    countback_string DESC,
                    running_order ASC NULLS LAST,
                    song_id ASC
            ) AS place'
    );

    IF updated_definition = definition THEN
        IF position('running_order ASC NULLS LAST' IN definition) = 0 THEN
            RAISE EXCEPTION 'Could not update result tiebreak in refresh_show_results_for_mode';
        END IF;
    ELSE
        EXECUTE updated_definition;
    END IF;
END;
$$;

-- Recalculate cached places so previously tied results receive the corrected order.
DO $$
DECLARE
    result_set record;
BEGIN
    FOR result_set IN
        SELECT DISTINCT show_id, result_mode
        FROM country_show_results
    LOOP
        PERFORM refresh_show_results_for_mode(result_set.show_id, result_set.result_mode);
    END LOOP;
END;
$$;
