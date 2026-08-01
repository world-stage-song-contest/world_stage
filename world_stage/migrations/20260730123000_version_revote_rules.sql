-- Give revote behavior its own immutable ruleset snapshot. v6 is the current
-- ownership-exclusion policy with the too-many-owned-entries exception.

ALTER TABLE voting_ruleset
    ADD COLUMN IF NOT EXISTS is_current_revote boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS idx_voting_ruleset_one_current_revote
    ON voting_ruleset (is_current_revote)
    WHERE is_current_revote;

INSERT INTO voting_ruleset (
    version, description, is_current, is_current_revote,
    penalizes_non_voters
)
VALUES (
    'v6',
    'Revote ownership exclusion with a ballot-capacity exception',
    false,
    true,
    true
)
ON CONFLICT (version) DO UPDATE
SET description = EXCLUDED.description,
    is_current_revote = EXCLUDED.is_current_revote,
    penalizes_non_voters = EXCLUDED.penalizes_non_voters;

ALTER TABLE show
    ADD COLUMN IF NOT EXISTS revote_ruleset_version text
        REFERENCES voting_ruleset (version) ON UPDATE RESTRICT ON DELETE RESTRICT;

UPDATE show
SET revote_ruleset_version = 'v6'
WHERE revote_ruleset_version IS NULL;

ALTER TABLE show
    ALTER COLUMN revote_ruleset_version SET NOT NULL;

CREATE OR REPLACE FUNCTION assign_current_voting_ruleset()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.voting_ruleset_version IS NULL THEN
        SELECT version
        INTO NEW.voting_ruleset_version
        FROM voting_ruleset
        WHERE is_current;

        IF NEW.voting_ruleset_version IS NULL THEN
            RAISE EXCEPTION 'No current voting ruleset is configured';
        END IF;
    END IF;
    IF NEW.revote_ruleset_version IS NULL THEN
        SELECT version
        INTO NEW.revote_ruleset_version
        FROM voting_ruleset
        WHERE is_current_revote;

        IF NEW.revote_ruleset_version IS NULL THEN
            RAISE EXCEPTION 'No current revote ruleset is configured';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION ballot_entry_rule(
    p_show_id bigint,
    p_result_mode text,
    p_voter_id bigint,
    p_country_id text,
    p_song_id bigint
)
RETURNS TABLE (
    rule_kind text,
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
            required_score := NULL;
            score_cap := 0;
        ELSE
            rule_kind := 'NORMAL';
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
                required_score := 1;
                score_cap := 1;
            ELSE
                rule_kind := 'NORMAL';
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        WHEN 'v2' THEN
            IF p_country_id = v_song_country_id THEN
                rule_kind := 'FORBIDDEN';
                required_score := NULL;
                score_cap := 0;
            ELSE
                rule_kind := 'NORMAL';
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        WHEN 'v3' THEN
            IF p_country_id = v_song_country_id
               OR v_song_submitter_id = p_voter_id THEN
                rule_kind := 'FORBIDDEN';
                required_score := NULL;
                score_cap := 0;
            ELSE
                rule_kind := 'NORMAL';
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        WHEN 'v4', 'v5' THEN
            IF v_song_submitter_id = p_voter_id THEN
                rule_kind := 'FORBIDDEN';
                required_score := NULL;
                score_cap := 0;
            ELSE
                rule_kind := 'NORMAL';
                required_score := NULL;
                score_cap := v_max_score;
            END IF;
        ELSE
            RAISE EXCEPTION 'Unsupported voting ruleset: %', v_ruleset;
    END CASE;

    RETURN NEXT;
END;
$$ LANGUAGE plpgsql STABLE;
