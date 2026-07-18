-- Bias calculations use original ballots by default.  When requested, a
-- Revote ballot takes the place of that voter's original ballot for its show.
CREATE OR REPLACE FUNCTION bias_vote_sets(p_include_revotes boolean DEFAULT false)
RETURNS SETOF vote_set
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (vs.show_id, vs.voter_id) vs.*
    FROM vote_set vs
    WHERE vs.result_mode = 'official'
       OR (p_include_revotes AND vs.result_mode = 'revote')
    ORDER BY vs.show_id, vs.voter_id, (vs.result_mode = 'revote') DESC
$$;

DO $$
DECLARE
    function_name regprocedure;
    definition text;
BEGIN
    FOREACH function_name IN ARRAY ARRAY[
        'user_country_bias(bigint,bigint,bigint)'::regprocedure,
        'user_submitter_bias(bigint,bigint,bigint,boolean)'::regprocedure,
        'country_voter_bias(text,bigint,bigint)'::regprocedure,
        'submitter_voter_bias(bigint,bigint,bigint,boolean)'::regprocedure
    ] LOOP
        definition := pg_get_functiondef(function_name);
        definition := regexp_replace(
            definition,
            '(p_include_specials boolean DEFAULT [^,\n]+)(\))',
            E'\\1,\n    p_include_revotes boolean DEFAULT false\\2'
        );
        definition := regexp_replace(
            definition,
            '(p_year_to bigint DEFAULT [^,\n]+)(\))',
            E'\\1,\n    p_include_revotes boolean DEFAULT false\\2'
        );
        definition := regexp_replace(
            definition, '\mvote_set\M', 'bias_vote_sets(p_include_revotes)', 'g'
        );
        EXECUTE definition;
    END LOOP;
END $$;
