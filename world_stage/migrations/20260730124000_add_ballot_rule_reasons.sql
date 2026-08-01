-- Expose why a ballot-entry rule applies so clients do not reconstruct rule
-- semantics from ownership, flags, or ruleset versions.

DROP FUNCTION IF EXISTS ballot_entry_rule(bigint, text, bigint, text, bigint);
CREATE FUNCTION ballot_entry_rule(
    p_show_id bigint,
    p_result_mode text,
    p_voter_id bigint,
    p_country_id text,
    p_song_id bigint
)
RETURNS TABLE (
    rule_kind text,
    rule_reason text,
    required_score integer,
    score_cap integer
) AS $$
DECLARE
    v_ruleset text;
    v_revote_ruleset text;
    v_max_score integer;
    v_scored_positions integer;
    v_song_submitter_id bigint;
    v_song_country_id text;
    v_entry_count integer;
    v_owned_entry_count integer;
    v_revote_allow_all boolean := false;
BEGIN
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;

    SELECT
        sh.voting_ruleset_version,
        sh.revote_ruleset_version,
        MAX(p.score),
        COUNT(p.id)
    INTO v_ruleset, v_revote_ruleset, v_max_score, v_scored_positions
    FROM show sh
    JOIN point p ON p.point_system_id = sh.point_system_id
    WHERE sh.id = p_show_id
    GROUP BY sh.voting_ruleset_version, sh.revote_ruleset_version;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Show % does not exist or has no configured points', p_show_id;
    END IF;

    SELECT s.submitter_id, s.country_id
    INTO v_song_submitter_id, v_song_country_id
    FROM song_show ss
    JOIN song s ON s.id = ss.song_id
    WHERE ss.show_id = p_show_id
      AND s.id = p_song_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Song % does not belong to show %', p_song_id, p_show_id;
    END IF;

    IF p_result_mode = 'revote' THEN
        IF v_revote_ruleset <> 'v6' THEN
            RAISE EXCEPTION 'Unsupported revote ruleset: %', v_revote_ruleset;
        END IF;

        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE s.submitter_id = p_voter_id)
        INTO v_entry_count, v_owned_entry_count
        FROM song_show ss
        JOIN song s ON s.id = ss.song_id
        WHERE ss.show_id = p_show_id;

        v_revote_allow_all :=
            v_entry_count - v_owned_entry_count < v_scored_positions;

        IF NOT v_revote_allow_all
           AND v_song_submitter_id = p_voter_id THEN
            rule_kind := 'FORBIDDEN';
            rule_reason := 'owner';
            required_score := NULL;
            score_cap := 0;
        ELSE
            rule_kind := 'NORMAL';
            rule_reason := NULL;
            required_score := NULL;
            score_cap := v_max_score;
        END IF;
        RETURN NEXT;
        RETURN;
    END IF;

    CASE v_ruleset
        WHEN 'v1' THEN
            IF p_country_id = v_song_country_id THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM show sh
                    JOIN point p ON p.point_system_id = sh.point_system_id
                    WHERE sh.id = p_show_id AND p.score = 1
                ) THEN
                    RAISE EXCEPTION
                        'Voting ruleset v1 requires a 1-point position in show %',
                        p_show_id;
                END IF;
                rule_kind := 'FORCED';
                rule_reason := 'flag';
                required_score := 1;
                score_cap := 1;
            ELSE
                rule_kind := 'NORMAL';
                rule_reason := NULL;
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        WHEN 'v2' THEN
            IF p_country_id = v_song_country_id THEN
                rule_kind := 'FORBIDDEN';
                rule_reason := 'flag';
                required_score := NULL;
                score_cap := 0;
            ELSE
                rule_kind := 'NORMAL';
                rule_reason := NULL;
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        WHEN 'v3' THEN
            IF p_country_id = v_song_country_id
               OR v_song_submitter_id = p_voter_id THEN
                rule_kind := 'FORBIDDEN';
                rule_reason := CASE
                    WHEN p_country_id = v_song_country_id
                         AND v_song_submitter_id = p_voter_id
                        THEN 'flag_and_owner'
                    WHEN p_country_id = v_song_country_id THEN 'flag'
                    ELSE 'owner'
                END;
                required_score := NULL;
                score_cap := 0;
            ELSE
                rule_kind := 'NORMAL';
                rule_reason := NULL;
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        WHEN 'v4', 'v5' THEN
            IF v_song_submitter_id = p_voter_id THEN
                rule_kind := 'FORBIDDEN';
                rule_reason := 'owner';
                required_score := NULL;
                score_cap := 0;
            ELSE
                rule_kind := 'NORMAL';
                rule_reason := NULL;
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        ELSE
            RAISE EXCEPTION 'Unsupported voting ruleset: %', v_ruleset;
    END CASE;

    RETURN NEXT;
END;
$$ LANGUAGE plpgsql STABLE;
