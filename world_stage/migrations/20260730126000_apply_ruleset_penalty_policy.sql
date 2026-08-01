-- Make non-voter penalties consume the same immutable ruleset snapshots as
-- ballot eligibility and adjusted result calculations.

CREATE OR REPLACE FUNCTION voting_ruleset_penalizes_non_voters(
    p_show_id bigint,
    p_result_mode text
)
RETURNS boolean AS $$
    SELECT CASE p_result_mode
        WHEN 'official' THEN official_rules.penalizes_non_voters
        WHEN 'revote' THEN revote_rules.penalizes_non_voters
        ELSE NULL
    END
    FROM show sh
    JOIN voting_ruleset official_rules
      ON official_rules.version = sh.voting_ruleset_version
    JOIN voting_ruleset revote_rules
      ON revote_rules.version = sh.revote_ruleset_version
    WHERE sh.id = p_show_id;
$$ LANGUAGE sql STABLE;

DO $$
DECLARE
    definition text;
    old_penalty_case text :=
        'CASE
                        WHEN p_result_mode = ''revote'' THEN ss.revote_penalty
                        ELSE ss.penalty
                    END';
    new_penalty_case text :=
        'CASE
                        WHEN NOT voting_ruleset_penalizes_non_voters(
                            si.id, p_result_mode
                        ) THEN 0
                        WHEN p_result_mode = ''revote'' THEN ss.revote_penalty
                        ELSE ss.penalty
                    END';
BEGIN
    SELECT pg_get_functiondef(
        'refresh_show_results_for_mode(bigint,text)'::regprocedure
    )
    INTO definition;

    IF position('voting_ruleset_penalizes_non_voters' IN definition) = 0 THEN
        IF position(old_penalty_case IN definition) = 0 THEN
            RAISE EXCEPTION
                'Could not apply voting-ruleset penalty policy to result refresh';
        END IF;
        EXECUTE replace(definition, old_penalty_case, new_penalty_case);
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION refresh_revote_penalties(p_show_id bigint)
RETURNS void AS $$
DECLARE
    max_point integer;
    penalties_enabled boolean;
BEGIN
    SELECT
        MAX(point.score),
        BOOL_OR(rules.penalizes_non_voters)
    INTO max_point, penalties_enabled
    FROM show
    JOIN point ON point.point_system_id = show.point_system_id
    JOIN voting_ruleset rules ON rules.version = show.revote_ruleset_version
    WHERE show.id = p_show_id;

    UPDATE song_show ss
    SET revote_penalty = CASE
        WHEN NOT COALESCE(penalties_enabled, false) THEN 0
        WHEN song.submitter_id IS NULL THEN 0
        WHEN EXISTS (
            SELECT 1
            FROM vote_set
            WHERE show_id = p_show_id
              AND voter_id = song.submitter_id
              AND result_mode = 'official'
        ) THEN 0
        WHEN EXISTS (
            SELECT 1
            FROM vote_set
            WHERE show_id = p_show_id
              AND voter_id = song.submitter_id
              AND result_mode = 'revote'
        ) THEN 0
        ELSE COALESCE(max_point, 0)
    END
    FROM song
    WHERE ss.show_id = p_show_id AND song.id = ss.song_id;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    show_row record;
BEGIN
    FOR show_row IN
        SELECT DISTINCT sh.id
        FROM show sh
        JOIN voting_ruleset rules
          ON rules.version = sh.voting_ruleset_version
        JOIN song_show ss ON ss.show_id = sh.id
        WHERE NOT rules.penalizes_non_voters
          AND ss.penalty <> 0
    LOOP
        UPDATE song_show
        SET penalty = 0
        WHERE show_id = show_row.id AND penalty <> 0;
        PERFORM refresh_show_results_for_mode(show_row.id, 'official');
    END LOOP;

    FOR show_row IN
        SELECT DISTINCT sh.id
        FROM show sh
        JOIN voting_ruleset rules
          ON rules.version = sh.revote_ruleset_version
        JOIN song_show ss ON ss.show_id = sh.id
        WHERE NOT rules.penalizes_non_voters
          AND ss.revote_penalty <> 0
    LOOP
        PERFORM refresh_revote_penalties(show_row.id);
        IF EXISTS (
            SELECT 1
            FROM country_show_results
            WHERE show_id = show_row.id AND result_mode = 'revote'
        ) THEN
            PERFORM refresh_show_results_for_mode(show_row.id, 'revote');
        END IF;
    END LOOP;
END;
$$;
