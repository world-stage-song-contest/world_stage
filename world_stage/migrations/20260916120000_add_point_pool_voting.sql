BEGIN;

ALTER TABLE point_system
    ADD COLUMN kind text NOT NULL DEFAULT 'ranked' CHECK (kind IN ('ranked', 'pool')),
    ADD COLUMN metadata jsonb;

UPDATE point_system ps
SET metadata = jsonb_build_object('points', COALESCE((
    SELECT jsonb_agg(p.score ORDER BY p.place, p.id)
    FROM point p WHERE p.point_system_id = ps.id
), '[]'::jsonb));

CREATE OR REPLACE FUNCTION point_pool_required_items(settings jsonb)
RETURNS integer
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT GREATEST((settings->>'min_items')::integer,
        CASE WHEN (settings->>'require_all_points')::boolean
             THEN CEIL((settings->>'total_points')::numeric
                       / COALESCE((settings->>'max_points_per_item')::integer,
                                  (settings->>'total_points')::integer))::integer
             ELSE 0 END);
$$;

CREATE OR REPLACE FUNCTION valid_point_system_metadata(p_kind text, p_metadata jsonb)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    value jsonb;
    key text;
BEGIN
    IF jsonb_typeof(p_metadata) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF p_kind = 'ranked' THEN
        IF jsonb_typeof(p_metadata->'points') IS DISTINCT FROM 'array' THEN
            RETURN false;
        END IF;
        FOR value IN SELECT jsonb_array_elements(p_metadata->'points') LOOP
            IF jsonb_typeof(value) <> 'number' OR value::text !~ '^[0-9]+$'
               OR value::numeric NOT BETWEEN 1 AND 2147483647 THEN
                RETURN false;
            END IF;
        END LOOP;
        RETURN (SELECT COUNT(*) = COUNT(DISTINCT score)
                FROM jsonb_array_elements(p_metadata->'points') AS scores(score));
    ELSIF p_kind = 'pool' THEN
        FOREACH key IN ARRAY ARRAY['total_points', 'min_items', 'max_items',
                                 'min_points_per_item', 'max_points_per_item']
        LOOP
            value := p_metadata->key;
            IF key = 'min_points_per_item' THEN
                value := COALESCE(value, '1'::jsonb);
            END IF;
            IF key IN ('max_items', 'max_points_per_item')
               AND (value IS NULL OR value = 'null'::jsonb) THEN
                CONTINUE;
            END IF;
            IF jsonb_typeof(value) IS DISTINCT FROM 'number' OR value::text !~ '^[0-9]+$'
               OR value::numeric NOT BETWEEN (CASE WHEN key = 'min_items' THEN 0 ELSE 1 END)
                                          AND 2147483647 THEN
                RETURN false;
            END IF;
        END LOOP;
        IF jsonb_typeof(p_metadata->'require_all_points') IS DISTINCT FROM 'boolean' THEN
            RETURN false;
        END IF;
        RETURN COALESCE((p_metadata->>'min_points_per_item')::integer, 1) <= LEAST(
                   (p_metadata->>'max_points_per_item')::integer,
                   (p_metadata->>'total_points')::integer)
           AND point_pool_required_items(p_metadata) <= COALESCE(
                   (p_metadata->>'max_items')::integer, (p_metadata->>'total_points')::integer)
           AND point_pool_required_items(p_metadata)::bigint
                   * COALESCE((p_metadata->>'min_points_per_item')::integer, 1)
               <= (p_metadata->>'total_points')::integer;
    END IF;
    RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION ranked_points_max(points jsonb)
RETURNS integer
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT MAX(score::integer) FROM jsonb_array_elements_text(points) AS scores(score);
$$;

CREATE OR REPLACE FUNCTION ranked_points_total(points jsonb)
RETURNS bigint
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT COALESCE(SUM(score::integer), 0)
    FROM jsonb_array_elements_text(points) AS scores(score);
$$;

ALTER TABLE point_system
    ALTER COLUMN metadata SET NOT NULL,
    ALTER COLUMN metadata SET DEFAULT '{"points": []}',
    ADD CONSTRAINT point_system_metadata_valid CHECK (valid_point_system_metadata(kind, metadata)),
    ADD COLUMN max_score integer GENERATED ALWAYS AS (
        CASE WHEN kind = 'pool' THEN LEAST(
            (metadata->>'max_points_per_item')::integer,
            (metadata->>'total_points')::integer
                - GREATEST(point_pool_required_items(metadata)::bigint - 1, 0)
                  * COALESCE((metadata->>'min_points_per_item')::integer, 1))
        ELSE ranked_points_max(metadata->'points') END
    ) STORED,
    ADD COLUMN total_points bigint GENERATED ALWAYS AS (
        CASE WHEN kind = 'pool' THEN (metadata->>'total_points')::bigint
        ELSE ranked_points_total(metadata->'points') END
    ) STORED,
    ADD COLUMN required_items integer GENERATED ALWAYS AS (
        CASE WHEN kind = 'pool' THEN point_pool_required_items(metadata)
        ELSE jsonb_array_length(metadata->'points') END
    ) STORED;

CREATE OR REPLACE FUNCTION ballot_entry_rule(
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
    v_min_score integer;
    v_scored_positions integer;
    v_song_submitter_id bigint;
    v_song_country_id text;
    v_entry_count integer;
    v_owned_entry_count integer;
    v_national_final_id bigint;
    v_allow_owned_entries boolean := false;
BEGIN
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;

    SELECT
        sh.voting_ruleset_version,
        sh.revote_ruleset_version,
        p.max_score,
        p.required_items,
        CASE WHEN p.kind = 'pool'
             THEN COALESCE((p.metadata->>'min_points_per_item')::integer, 1) ELSE 1 END,
        sh.national_final_id
    INTO v_ruleset, v_revote_ruleset, v_max_score, v_scored_positions,
         v_min_score, v_national_final_id
    FROM show sh
    JOIN point_system p ON p.id = sh.point_system_id
    WHERE sh.id = p_show_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Show % does not exist or has no configured points', p_show_id;
    END IF;

    SELECT s.submitter_id, s.country_id
    INTO v_song_submitter_id, v_song_country_id
    FROM song_show ss
    JOIN current_song s ON s.id = ss.song_id
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
        JOIN current_song s ON s.id = ss.song_id
        WHERE ss.show_id = p_show_id;

        v_allow_owned_entries :=
            v_entry_count - v_owned_entry_count < v_scored_positions;

        IF NOT v_allow_owned_entries
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

    -- National finals use a single ordinary ballot. Country affiliation and
    -- ownership of the national final do not restrict voting. A submitter's
    -- own candidates are excluded only when enough other candidates remain to
    -- fill every scoring position, matching the revote capacity exception.
    IF v_national_final_id IS NOT NULL THEN
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE s.submitter_id = p_voter_id)
        INTO v_entry_count, v_owned_entry_count
        FROM song_show ss
        JOIN current_song s ON s.id = ss.song_id
        WHERE ss.show_id = p_show_id;

        v_allow_owned_entries :=
            v_entry_count - v_owned_entry_count < v_scored_positions;

        IF NOT v_allow_owned_entries
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
                    JOIN point_system ps ON ps.id = sh.point_system_id
                    WHERE sh.id = p_show_id AND (ps.metadata->'points' @> '[1]'::jsonb OR ps.kind = 'pool')
                ) THEN
                    RAISE EXCEPTION
                        'Voting ruleset v1 requires a 1-point position in show %',
                        p_show_id;
                END IF;
                rule_kind := 'FORCED';
                rule_reason := 'flag';
                required_score := v_min_score;
                score_cap := v_min_score;
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

CREATE OR REPLACE FUNCTION ballot_entry_rule_matrix(
    p_show_id bigint,
    p_result_mode text
)
RETURNS TABLE (
    vote_set_id bigint,
    voter_id bigint,
    country_id text,
    song_id bigint,
    rule_kind text,
    rule_reason text,
    required_score integer,
    score_cap integer
) AS $$
DECLARE
    v_ruleset text;
    v_revote_ruleset text;
    v_max_score integer;
    v_min_score integer;
    v_scored_positions integer;
    v_national_final_id bigint;
BEGIN
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;

    SELECT
        sh.voting_ruleset_version,
        sh.revote_ruleset_version,
        p.max_score,
        p.required_items,
        CASE WHEN p.kind = 'pool'
             THEN COALESCE((p.metadata->>'min_points_per_item')::integer, 1) ELSE 1 END,
        sh.national_final_id
    INTO v_ruleset, v_revote_ruleset, v_max_score, v_scored_positions,
         v_min_score, v_national_final_id
    FROM show sh
    JOIN point_system p ON p.id = sh.point_system_id
    WHERE sh.id = p_show_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Show % does not exist or has no configured points', p_show_id;
    END IF;
    IF p_result_mode = 'revote' AND v_revote_ruleset <> 'v6' THEN
        RAISE EXCEPTION 'Unsupported revote ruleset: %', v_revote_ruleset;
    END IF;
    IF p_result_mode = 'official'
       AND v_national_final_id IS NULL
       AND v_ruleset NOT IN ('v1', 'v2', 'v3', 'v4', 'v5') THEN
        RAISE EXCEPTION 'Unsupported voting ruleset: %', v_ruleset;
    END IF;

    RETURN QUERY
    WITH entries AS MATERIALIZED (
        SELECT
            song.id AS song_id,
            song.country_id AS entry_country_id,
            data.submitter_id
        FROM song_show ss
        JOIN song ON song.id = ss.song_id
        JOIN LATERAL (
            SELECT revision.submitter_id, revision.title,
                   revision.artist_credit_set_id
            FROM song_data revision
            WHERE revision.song_id = song.id
               OR (
                   revision.song_id IS NULL
                   AND revision.country_id = song.country_id
                   AND revision.year_id = song.year_id
                   AND revision.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY revision.created_at DESC, revision.id DESC
            LIMIT 1
        ) data ON data.title IS NOT NULL
              AND data.artist_credit_set_id IS NOT NULL
        WHERE ss.show_id = p_show_id
    ),
    vote_sets AS MATERIALIZED (
        SELECT vs.id, vs.voter_id, vs.country_id
        FROM effective_show_vote_sets(p_show_id, p_result_mode) vs
    ),
    entry_count AS (
        SELECT COUNT(*)::integer AS value FROM entries
    ),
    ownership AS (
        SELECT
            vs.voter_id,
            COUNT(e.song_id) FILTER (
                WHERE e.submitter_id = vs.voter_id
            )::integer AS owned_entry_count
        FROM (
            SELECT DISTINCT source.voter_id
            FROM vote_sets source
        ) vs
        CROSS JOIN entries e
        GROUP BY vs.voter_id
    )
    SELECT
        vs.id,
        vs.voter_id,
        vs.country_id,
        e.song_id,
        CASE
            WHEN (
                p_result_mode = 'revote' OR v_national_final_id IS NOT NULL
            ) AND e.submitter_id = vs.voter_id
              AND ec.value - COALESCE(o.owned_entry_count, 0)
                    >= v_scored_positions
                THEN 'FORBIDDEN'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v1'
              AND vs.country_id = e.entry_country_id
                THEN 'FORCED'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v2'
              AND vs.country_id = e.entry_country_id
                THEN 'FORBIDDEN'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v3'
              AND (
                  vs.country_id = e.entry_country_id
                  OR e.submitter_id = vs.voter_id
              )
                THEN 'FORBIDDEN'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset IN ('v4', 'v5')
              AND e.submitter_id = vs.voter_id
                THEN 'FORBIDDEN'
            ELSE 'NORMAL'
        END AS rule_kind,
        CASE
            WHEN (
                p_result_mode = 'revote' OR v_national_final_id IS NOT NULL
            ) AND e.submitter_id = vs.voter_id
              AND ec.value - COALESCE(o.owned_entry_count, 0)
                    >= v_scored_positions
                THEN 'owner'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v1'
              AND vs.country_id = e.entry_country_id
                THEN 'flag'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v2'
              AND vs.country_id = e.entry_country_id
                THEN 'flag'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v3'
              AND vs.country_id = e.entry_country_id
              AND e.submitter_id = vs.voter_id
                THEN 'flag_and_owner'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v3'
              AND vs.country_id = e.entry_country_id
                THEN 'flag'
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset IN ('v3', 'v4', 'v5')
              AND e.submitter_id = vs.voter_id
                THEN 'owner'
            ELSE NULL
        END AS rule_reason,
        CASE
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v1'
              AND vs.country_id = e.entry_country_id
                THEN v_min_score
            ELSE NULL
        END AS required_score,
        CASE
            WHEN (
                p_result_mode = 'revote' OR v_national_final_id IS NOT NULL
            ) AND e.submitter_id = vs.voter_id
              AND ec.value - COALESCE(o.owned_entry_count, 0)
                    >= v_scored_positions
                THEN 0
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND v_ruleset = 'v1'
              AND vs.country_id = e.entry_country_id
                THEN v_min_score
            WHEN p_result_mode = 'official'
              AND v_national_final_id IS NULL
              AND (
                  (v_ruleset = 'v2' AND vs.country_id = e.entry_country_id)
                  OR (
                      v_ruleset = 'v3'
                      AND (
                          vs.country_id = e.entry_country_id
                          OR e.submitter_id = vs.voter_id
                      )
                  )
                  OR (
                      v_ruleset IN ('v4', 'v5')
                      AND e.submitter_id = vs.voter_id
                  )
              )
                THEN 0
            ELSE v_max_score
        END AS score_cap
    FROM vote_sets vs
    CROSS JOIN entries e
    CROSS JOIN entry_count ec
    LEFT JOIN ownership o ON o.voter_id = vs.voter_id;

    -- Match ballot_entry_rule's v1 configuration error, but only when the
    -- matrix actually contains a flag-country cell that requires one point.
    IF p_result_mode = 'official'
       AND v_national_final_id IS NULL
       AND v_ruleset = 'v1'
       AND NOT EXISTS (
           SELECT 1 FROM show sh
           JOIN point_system ps ON ps.id = sh.point_system_id
           WHERE sh.id = p_show_id AND (ps.metadata->'points' @> '[1]'::jsonb OR ps.kind = 'pool')
       )
       AND EXISTS (
           SELECT 1
           FROM effective_show_vote_sets(p_show_id, p_result_mode) vs
           JOIN song_show ss ON ss.show_id = p_show_id
           JOIN song s ON s.id = ss.song_id
           WHERE vs.country_id = s.country_id
       ) THEN
        RAISE EXCEPTION
            'Voting ruleset v1 requires a 1-point position in show %',
            p_show_id;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION refresh_show_results_for_mode(
    p_show_id bigint, p_result_mode text
)
RETURNS void AS $$
DECLARE
    v_max_point integer;
    v_system_sum bigint;
    v_penalizes_non_voters boolean;
    v_inserted integer;
BEGIN
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;

    SELECT p.max_score, p.total_points
    INTO v_max_point, v_system_sum
    FROM show sh
    JOIN point_system p ON p.id = sh.point_system_id
    WHERE sh.id = p_show_id;

    SELECT voting_ruleset_penalizes_non_voters(p_show_id, p_result_mode)
    INTO v_penalizes_non_voters;

    DELETE FROM country_show_results
    WHERE show_id = p_show_id AND result_mode = p_result_mode;

    WITH si AS MATERIALIZED (
        SELECT
            sh.id, sh.year_id,
            sh.short_name, sh.show_name
        FROM show sh
        WHERE sh.id = p_show_id
    ),
    entries AS MATERIALIZED (
        SELECT
            song.id AS song_id,
            song.country_id,
            data.submitter_id,
            c.name AS country_name,
            ss.running_order,
            ss.penalty,
            ss.revote_penalty
        FROM song_show ss
        JOIN song ON song.id = ss.song_id
        JOIN LATERAL (
            SELECT revision.submitter_id, revision.title,
                   revision.artist_credit_set_id
            FROM song_data revision
            WHERE revision.song_id = song.id
               OR (
                   revision.song_id IS NULL
                   AND revision.country_id = song.country_id
                   AND revision.year_id = song.year_id
                   AND revision.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY revision.created_at DESC, revision.id DESC
            LIMIT 1
        ) data ON data.title IS NOT NULL
              AND data.artist_credit_set_id IS NOT NULL
        JOIN country c ON c.id = song.country_id AND c.is_participating
        WHERE ss.show_id = p_show_id
    ),
    all_vote_sets AS MATERIALIZED (
        SELECT * FROM effective_show_vote_sets(p_show_id, p_result_mode)
    ),
    rule_matrix AS MATERIALIZED (
        SELECT * FROM ballot_entry_rule_matrix(p_show_id, p_result_mode)
    ),
    voters_by_country AS (
        SELECT vs.country_id, COUNT(*) AS cnt
        FROM all_vote_sets vs
        WHERE vs.country_id IS NOT NULL
        GROUP BY vs.country_id
    ),
    totals AS (
        SELECT COUNT(*) AS total_valid FROM all_vote_sets
    ),
    point_counts AS (
        SELECT e.song_id, e.country_id, v.score, COUNT(*) AS cnt
        FROM entries e
        JOIN vote v ON v.song_id = e.song_id
        JOIN rule_matrix rule
          ON rule.vote_set_id = v.vote_set_id
         AND rule.song_id = v.song_id
        WHERE rule.rule_kind <> 'FORBIDDEN'
          AND (
              rule.rule_kind <> 'FORCED'
              OR v.score = rule.required_score
          )
        GROUP BY e.song_id, e.country_id, v.score
    ),
    entry_caps AS (
        SELECT rule.song_id, SUM(rule.score_cap)::integer AS score_cap
        FROM rule_matrix rule
        GROUP BY rule.song_id
    ),
    aggregated AS (
        SELECT
            e.country_id,
            e.country_name,
            si.id AS show_id,
            e.song_id,
            e.running_order,
            si.show_name,
            si.short_name,
            si.year_id,
            GREATEST(
                COALESCE(SUM(pc.score * pc.cnt), 0)
                - COALESCE(
                    CASE
                        WHEN NOT v_penalizes_non_voters THEN 0
                        WHEN p_result_mode = 'revote' THEN e.revote_penalty
                        ELSE e.penalty
                    END,
                    0
                ),
                0
            ) AS total_points,
            COALESCE(SUM(pc.cnt), 0) AS total_votes_received,
            COALESCE(
                JSONB_OBJECT_AGG(pc.score::text, pc.cnt ORDER BY pc.score DESC)
                    FILTER (WHERE pc.score IS NOT NULL),
                '{}'::jsonb
            ) AS point_distribution,
            COALESCE(
                STRING_AGG(
                    LPAD(pc.score::text, 10, '0') || ':'
                        || LPAD(pc.cnt::text, 20, '0'),
                    ',' ORDER BY pc.score DESC
                ) FILTER (WHERE pc.score IS NOT NULL),
                ''
            ) AS countback_string,
            (SELECT total_valid FROM totals) AS total_voters,
            COALESCE(ec.score_cap, 0) AS adjusted_max_possible_points_calc,
            CASE
                WHEN v_max_point IS NULL THEN 0
                WHEN si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979
                    THEN v_max_point * (SELECT total_valid FROM totals)
                WHEN (si.year_id BETWEEN 1966 AND 1978)
                     OR (si.year_id = 1965 AND si.short_name = 'f')
                    THEN v_max_point * GREATEST(
                        (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0),
                        0
                    )
                WHEN si.year_id = 1960
                    THEN v_max_point * GREATEST(
                        (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0),
                        0
                    ) + COALESCE(vbc.cnt, 0)
                ELSE v_max_point * GREATEST(
                    (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0),
                    0
                )
            END AS max_possible_points_calc
        FROM si
        CROSS JOIN entries e
        LEFT JOIN point_counts pc ON pc.song_id = e.song_id
        LEFT JOIN voters_by_country vbc ON vbc.country_id = e.country_id
        LEFT JOIN entry_caps ec ON ec.song_id = e.song_id
        GROUP BY
            e.country_id, e.country_name, si.id, e.song_id, e.running_order,
            e.penalty, e.revote_penalty, si.show_name, si.short_name,
            si.year_id, vbc.cnt, ec.score_cap
    ),
    ranked AS (
        SELECT
            a.*,
            ROW_NUMBER() OVER (
                ORDER BY
                    total_points DESC,
                    total_votes_received DESC,
                    countback_string DESC,
                    running_order ASC NULLS LAST,
                    song_id ASC
            ) AS place,
            COUNT(*) OVER () AS total_countries
        FROM aggregated a
    ),
    normalized AS (
        SELECT
            r.*,
            v_system_sum::numeric * r.total_voters
                / NULLIF(r.total_countries, 0) AS points_midpoint_calc
        FROM ranked r
    )
    INSERT INTO country_show_results (
        country_id, country_name, show_id, show_name, short_name, year_id,
        song_id, running_order, total_points, total_votes_received,
        point_distribution, place, total_countries, placement_percentage,
        max_possible_points, points_percentage, adjusted_points_percentage,
        adjusted_max_possible_points, points_midpoint, entry_status,
        special_qualifier, max_pts, total_voters, result_mode
    )
    SELECT
        n.country_id,
        n.country_name,
        n.show_id,
        n.show_name,
        n.short_name,
        n.year_id,
        n.song_id,
        n.running_order,
        n.total_points,
        n.total_votes_received,
        n.point_distribution,
        n.place,
        n.total_countries,
        ROUND(
            CASE
                WHEN n.place = n.total_countries THEN 0
                WHEN n.total_countries > 1
                    THEN ((n.total_countries - n.place)::numeric
                        / (n.total_countries - 1)) * 100
                ELSE 100
            END,
            2
        ),
        COALESCE(n.max_possible_points_calc, 0),
        ROUND(
            COALESCE(
                n.total_points::numeric
                    / NULLIF(n.max_possible_points_calc::numeric, 0),
                0
            ) * 100,
            2
        ),
        ROUND(
            calculate_adjusted_points_percentage(
                n.total_points::numeric,
                n.points_midpoint_calc,
                n.adjusted_max_possible_points_calc::numeric
            ),
            2
        ),
        n.adjusted_max_possible_points_calc,
        ROUND(COALESCE(n.points_midpoint_calc, 0), 6),
        CASE WHEN EXISTS (
            SELECT 1 FROM show_progression
            WHERE source_show_id = n.show_id
        ) THEN show_result_progression_status(
            n.show_id, n.song_id, n.place, p_result_mode
        ) ELSE NULL END,
        show_result_special_qualifier(n.show_id, n.song_id, p_result_mode),
        v_max_point,
        n.total_voters,
        p_result_mode
    FROM normalized n;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Refreshed % results for show %; rows=%',
        p_result_mode, p_show_id, v_inserted;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION refresh_revote_penalties(p_show_id bigint)
RETURNS void AS $$
DECLARE
    max_point integer;
    penalties_enabled boolean;
BEGIN
    SELECT
        point.max_score,
        rules.penalizes_non_voters
    INTO max_point, penalties_enabled
    FROM show
    JOIN point_system point ON point.id = show.point_system_id
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
    FROM current_song AS song
    WHERE ss.show_id = p_show_id AND song.id = ss.song_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION process_show_result_refresh_queue()
RETURNS TRIGGER AS $$
BEGIN
    -- A deferred refresh can outlive the show or its point configuration
    -- when either is removed later in the same transaction.
    IF NOT EXISTS (
        SELECT 1
        FROM show
        JOIN point_system point ON point.id = show.point_system_id
        WHERE show.id = NEW.show_id
    ) THEN
        DELETE FROM country_show_results
        WHERE show_id = NEW.show_id AND result_mode = NEW.result_mode;
        DELETE FROM show_result_refresh_queue
        WHERE show_id = NEW.show_id AND result_mode = NEW.result_mode;
        RETURN NEW;
    END IF;

    IF NEW.result_mode = 'revote' THEN
        PERFORM refresh_revote_penalties(NEW.show_id);
    END IF;

    PERFORM refresh_show_results_for_mode(NEW.show_id, NEW.result_mode);

    DELETE FROM show_result_refresh_queue
    WHERE show_id = NEW.show_id AND result_mode = NEW.result_mode;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION refresh_analytics_show_cache(
    p_show_id bigint,
    p_ballot_mode text
)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_year_id bigint;
    v_status text;
    v_include_revotes boolean;
BEGIN
    IF p_ballot_mode NOT IN ('official', 'effective') THEN
        RAISE EXCEPTION 'Unknown analytics ballot mode: %', p_ballot_mode;
    END IF;
    v_include_revotes := p_ballot_mode = 'effective';

    DELETE FROM taste_similarity_show_cache
    WHERE show_id = p_show_id AND ballot_mode = p_ballot_mode;
    DELETE FROM country_bias_show_cache
    WHERE show_id = p_show_id AND ballot_mode = p_ballot_mode;
    DELETE FROM submitter_bias_show_cache
    WHERE show_id = p_show_id AND ballot_mode = p_ballot_mode;

    SELECT year_id, status INTO v_year_id, v_status
    FROM show
    WHERE id = p_show_id;

    IF NOT FOUND OR v_status IS DISTINCT FROM 'full' THEN
        RETURN;
    END IF;

    -- One canonical row per voter pair.  The centred covariance and variances
    -- are additive across shows, which keeps every later filter inexpensive.
    INSERT INTO taste_similarity_show_cache (
        show_id, year_id, ballot_mode, voter_a_id, voter_b_id,
        co_voted_songs, covariance, variance_a, variance_b
    )
    WITH selected_sets AS MATERIALIZED (
        SELECT id, voter_id
        FROM bias_vote_sets(v_include_revotes)
        WHERE show_id = p_show_id
    ),
    pair_sums AS (
        SELECT a.voter_id AS voter_a_id,
            b.voter_id AS voter_b_id,
            COUNT(*)::numeric AS n,
            SUM(va.score)::numeric AS sum_a,
            SUM(vb.score)::numeric AS sum_b,
            SUM(va.score::numeric * vb.score) AS sum_ab,
            SUM(va.score::numeric * va.score) AS sum_aa,
            SUM(vb.score::numeric * vb.score) AS sum_bb
        FROM selected_sets a
        JOIN selected_sets b ON a.voter_id < b.voter_id
        JOIN vote va ON va.vote_set_id = a.id
        JOIN vote vb ON vb.vote_set_id = b.id AND vb.song_id = va.song_id
        JOIN song_show ss ON ss.show_id = p_show_id AND ss.song_id = va.song_id
        JOIN current_song s ON s.id = va.song_id
        WHERE s.submitter_id IS DISTINCT FROM a.voter_id
          AND s.submitter_id IS DISTINCT FROM b.voter_id
        GROUP BY a.voter_id, b.voter_id
    )
    SELECT p_show_id, v_year_id, p_ballot_mode, voter_a_id, voter_b_id,
        n::bigint,
        sum_ab - sum_a * sum_b / n,
        sum_aa - sum_a * sum_a / n,
        sum_bb - sum_b * sum_b / n
    FROM pair_sums;

    -- Country bias components for every voter/target pair in this show.
    INSERT INTO country_bias_show_cache (
        show_id, year_id, ballot_mode, voter_id, country_id,
        parts, votings_nonblank, votings_max,
        a_raw, e_raw, a_w, e_w, v_w
    )
    WITH selected_sets AS MATERIALIZED (
        SELECT id, voter_id
        FROM bias_vote_sets(v_include_revotes)
        WHERE show_id = p_show_id
    ),
    point_max AS (
        SELECT p.max_score
        FROM show sh
        JOIN point_system p ON p.id = sh.point_system_id
        WHERE sh.id = p_show_id
    ),
    sc AS (
        SELECT target.voter_id, s.country_id,
            COALESCE(SUM(v.score) FILTER (WHERE source.voter_id = target.voter_id), 0) AS actual,
            COALESCE(SUM(v.score) FILTER (WHERE source.voter_id <> target.voter_id), 0) AS others_t
        FROM selected_sets target
        CROSS JOIN selected_sets source
        JOIN vote v ON v.vote_set_id = source.id
        JOIN song_show ss ON ss.show_id = p_show_id AND ss.song_id = v.song_id
        JOIN current_song s ON s.id = v.song_id
        WHERE s.submitter_id <> target.voter_id
        GROUP BY target.voter_id, s.country_id
    ),
    pool AS (
        SELECT voter_id, SUM(actual) AS pool_v, SUM(others_t) AS others_all
        FROM sc
        GROUP BY voter_id
    ),
    vfield AS (
        SELECT target.voter_id, COUNT(DISTINCT s.id) AS n
        FROM selected_sets target
        JOIN song_show ss ON ss.show_id = p_show_id
        JOIN current_song s ON s.id = ss.song_id
        WHERE s.submitter_id <> target.voter_id
        GROUP BY target.voter_id
    ),
    agg AS (
        SELECT sc.voter_id, sc.country_id,
            SUM(sc.actual) AS a_raw,
            SUM(sc.others_t::numeric / NULLIF(pool.others_all, 0) * pool.pool_v) AS e_raw,
            SUM(vfield.n * sc.actual::numeric / NULLIF(pool.pool_v, 0)) AS a_w,
            SUM(vfield.n * sc.others_t::numeric / NULLIF(pool.others_all, 0)) AS e_w,
            SUM(vfield.n * vfield.n * sc.others_t::numeric
                / (NULLIF(pool.others_all, 0) * NULLIF(pool.pool_v, 0))) AS v_w
        FROM sc
        JOIN pool USING (voter_id)
        JOIN vfield USING (voter_id)
        WHERE pool.pool_v > 0
        GROUP BY sc.voter_id, sc.country_id
    ),
    parts AS (
        SELECT target.voter_id, s.country_id,
            COUNT(*) AS parts,
            COUNT(uv.id) AS votings_nonblank,
            COUNT(*) FILTER (WHERE uv.score = point_max.max_score) AS votings_max
        FROM selected_sets target
        JOIN song_show ss ON ss.show_id = p_show_id
        JOIN current_song s ON s.id = ss.song_id
        CROSS JOIN point_max
        LEFT JOIN vote uv ON uv.vote_set_id = target.id AND uv.song_id = s.id
        WHERE s.submitter_id <> target.voter_id
        GROUP BY target.voter_id, s.country_id
    )
    SELECT p_show_id, v_year_id, p_ballot_mode,
        parts.voter_id, parts.country_id,
        parts.parts, parts.votings_nonblank, parts.votings_max,
        COALESCE(agg.a_raw, 0)::bigint,
        COALESCE(agg.e_raw, 0),
        COALESCE(agg.a_w, 0),
        COALESCE(agg.e_w, 0),
        COALESCE(agg.v_w, 0)
    FROM parts
    LEFT JOIN agg USING (voter_id, country_id);

    -- Submitter bias uses the same additive components.  Reciprocal rows are
    -- retained even when this show has no matching `parts` row: a pair may
    -- become reportable because the submitter entered another shared show.
    INSERT INTO submitter_bias_show_cache (
        show_id, year_id, ballot_mode, voter_id, submitter_id,
        parts, votings_nonblank, votings_max,
        a_raw, e_raw, a_w, e_w, v_w,
        received, received_any, received_max
    )
    WITH selected_sets AS MATERIALIZED (
        SELECT id, voter_id
        FROM bias_vote_sets(v_include_revotes)
        WHERE show_id = p_show_id
    ),
    point_max AS (
        SELECT p.max_score
        FROM show sh
        JOIN point_system p ON p.id = sh.point_system_id
        WHERE sh.id = p_show_id
    ),
    sc AS (
        SELECT target.voter_id, s.submitter_id,
            COALESCE(SUM(v.score) FILTER (WHERE source.voter_id = target.voter_id), 0) AS actual,
            COALESCE(SUM(v.score) FILTER (WHERE source.voter_id <> target.voter_id), 0) AS others_t
        FROM selected_sets target
        CROSS JOIN selected_sets source
        JOIN vote v ON v.vote_set_id = source.id
        JOIN song_show ss ON ss.show_id = p_show_id AND ss.song_id = v.song_id
        JOIN current_song s ON s.id = v.song_id
        WHERE s.submitter_id <> target.voter_id
        GROUP BY target.voter_id, s.submitter_id
    ),
    pool AS (
        SELECT voter_id, SUM(actual) AS pool_v, SUM(others_t) AS others_all
        FROM sc
        GROUP BY voter_id
    ),
    vfield AS (
        SELECT target.voter_id, COUNT(DISTINCT s.id) AS n
        FROM selected_sets target
        JOIN song_show ss ON ss.show_id = p_show_id
        JOIN current_song s ON s.id = ss.song_id
        WHERE s.submitter_id <> target.voter_id
        GROUP BY target.voter_id
    ),
    agg AS (
        SELECT sc.voter_id, sc.submitter_id,
            SUM(sc.actual) AS a_raw,
            SUM(sc.others_t::numeric / NULLIF(pool.others_all, 0) * pool.pool_v) AS e_raw,
            SUM(vfield.n * sc.actual::numeric / NULLIF(pool.pool_v, 0)) AS a_w,
            SUM(vfield.n * sc.others_t::numeric / NULLIF(pool.others_all, 0)) AS e_w,
            SUM(vfield.n * vfield.n * sc.others_t::numeric
                / (NULLIF(pool.others_all, 0) * NULLIF(pool.pool_v, 0))) AS v_w
        FROM sc
        JOIN pool USING (voter_id)
        JOIN vfield USING (voter_id)
        WHERE pool.pool_v > 0
        GROUP BY sc.voter_id, sc.submitter_id
    ),
    parts AS (
        SELECT target.voter_id, s.submitter_id,
            COUNT(*) AS parts,
            COUNT(uv.id) AS votings_nonblank,
            COUNT(*) FILTER (WHERE uv.score = point_max.max_score) AS votings_max
        FROM selected_sets target
        JOIN song_show ss ON ss.show_id = p_show_id
        JOIN current_song s ON s.id = ss.song_id
        CROSS JOIN point_max
        LEFT JOIN vote uv ON uv.vote_set_id = target.id AND uv.song_id = s.id
        WHERE s.submitter_id <> target.voter_id
        GROUP BY target.voter_id, s.submitter_id
    ),
    reciprocal AS (
        SELECT s.submitter_id AS voter_id,
            source.voter_id AS submitter_id,
            SUM(v.score) AS received,
            COUNT(*) AS received_any,
            COUNT(*) FILTER (WHERE v.score = point_max.max_score) AS received_max
        FROM selected_sets source
        JOIN vote v ON v.vote_set_id = source.id
        JOIN song_show ss ON ss.show_id = p_show_id AND ss.song_id = v.song_id
        JOIN current_song s ON s.id = v.song_id
        CROSS JOIN point_max
        WHERE s.submitter_id IS NOT NULL
          AND s.submitter_id <> source.voter_id
        GROUP BY s.submitter_id, source.voter_id
    ),
    keys AS (
        SELECT voter_id, submitter_id FROM parts
        UNION
        SELECT voter_id, submitter_id FROM agg
        UNION
        SELECT voter_id, submitter_id FROM reciprocal
    )
    SELECT p_show_id, v_year_id, p_ballot_mode,
        keys.voter_id, keys.submitter_id,
        COALESCE(parts.parts, 0),
        COALESCE(parts.votings_nonblank, 0),
        COALESCE(parts.votings_max, 0),
        COALESCE(agg.a_raw, 0)::bigint,
        COALESCE(agg.e_raw, 0),
        COALESCE(agg.a_w, 0),
        COALESCE(agg.e_w, 0),
        COALESCE(agg.v_w, 0),
        COALESCE(reciprocal.received, 0)::bigint,
        COALESCE(reciprocal.received_any, 0),
        COALESCE(reciprocal.received_max, 0)
    FROM keys
    LEFT JOIN parts USING (voter_id, submitter_id)
    LEFT JOIN agg USING (voter_id, submitter_id)
    LEFT JOIN reciprocal USING (voter_id, submitter_id);
END;
$$;

CREATE OR REPLACE FUNCTION refresh_year_results(p_year_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM country_year_results WHERE year_id = p_year_id;

    WITH RECURSIVE paths(source_show_id, target_show_id, distance) AS (
        SELECT source_show_id, target_show_id, 1
        FROM show_progression
        UNION ALL
        SELECT paths.source_show_id, progression.target_show_id, paths.distance + 1
        FROM paths
        JOIN show_progression AS progression
          ON progression.source_show_id = paths.target_show_id
    ),
    show_tiers AS (
        SELECT show.id AS show_id,
               COALESCE(show.show_number, 0) AS show_number,
               COALESCE(MAX(paths.distance), 0) + 1 AS tier
        FROM show
        LEFT JOIN paths ON paths.source_show_id = show.id
        WHERE show.year_id = p_year_id AND show.national_final_id IS NULL
        GROUP BY show.id, show.show_number
    ),
    tiered_shows AS (
        SELECT show_tiers.*,
               COUNT(*) OVER (PARTITION BY tier) AS tier_show_count
        FROM show_tiers
    ),
    score_values AS (
        SELECT DISTINCT scores.value::integer AS score
        FROM country_show_results
        CROSS JOIN LATERAL jsonb_object_keys(point_distribution) AS scores(value)
        WHERE year_id = p_year_id AND result_mode = 'official'
    ),
    candidates AS (
        SELECT csr.*, tiered_shows.tier, tiered_shows.show_number,
               tiered_shows.tier_show_count,
               ROW_NUMBER() OVER (
                   PARTITION BY csr.song_id
                   ORDER BY tiered_shows.tier, csr.place, csr.show_id
               ) AS reached_order,
               csr.total_points::numeric
                   / NULLIF(csr.max_possible_points, 0) AS points_share,
               csr.total_votes_received::numeric
                   / NULLIF(csr.total_voters, 0) AS voter_share,
               (
                   SELECT ARRAY_AGG(
                       COALESCE(
                           (csr.point_distribution ->> score_values.score::text)::numeric,
                           0
                       ) / NULLIF(csr.total_voters, 0)
                       ORDER BY score_values.score DESC
                   )
                   FROM score_values
               ) AS countback
        FROM country_show_results AS csr
        JOIN tiered_shows ON tiered_shows.show_id = csr.show_id
        WHERE csr.year_id = p_year_id AND csr.result_mode = 'official'
    ),
    reached AS (
        SELECT * FROM candidates WHERE reached_order = 1
    ),
    all_ranked AS (
        SELECT country_id, country_name, year_id, song_id,
               ROW_NUMBER() OVER (
                   ORDER BY tier,
                            CASE WHEN tier_show_count = 1 THEN place END,
                            CASE WHEN tier_show_count > 1 THEN points_share END
                                DESC NULLS LAST,
                            CASE WHEN tier_show_count > 1 THEN voter_share END
                                DESC NULLS LAST,
                            CASE WHEN tier_show_count > 1 THEN countback END
                                DESC NULLS LAST,
                            running_order NULLS LAST, show_number, song_id
               )::integer AS place
        FROM reached
    ),
    totals AS (
    SELECT COUNT(*) AS total_countries FROM all_ranked
    )
    INSERT INTO country_year_results
    (country_id, country_name, year_id, song_id,
    place, total_countries, placement_percentage)
    SELECT
    ar.country_id,
    ar.country_name,
    ar.year_id,
    ar.song_id,
    ar.place,
    t.total_countries,
    CASE
    WHEN t.total_countries = 1 THEN 100
    ELSE ROUND(
    ((t.total_countries - ar.place)::numeric
    / (t.total_countries - 1)::numeric) * 100,
    3)
    END AS placement_percentage
    FROM all_ranked ar
    CROSS JOIN totals t
    ORDER BY ar.place;
END;
$$;

CREATE OR REPLACE FUNCTION queue_point_system_refresh()
RETURNS trigger AS $$
DECLARE
    v_show_id bigint;
BEGIN
    FOR v_show_id IN SELECT id FROM show WHERE point_system_id = NEW.id LOOP
        PERFORM queue_show_result_refresh(v_show_id, 'official');
        PERFORM queue_show_result_refresh(v_show_id, 'revote');
        PERFORM queue_analytics_show_refresh(v_show_id, 'official');
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION user_language_bias(
    p_user_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_revotes boolean DEFAULT false
)
RETURNS TABLE (
    language_id bigint,
    language_name text,
    parts bigint,
    votings_nonblank bigint,
    votings_max bigint,
    given bigint,
    expected numeric,
    bias numeric,
    log_ratio numeric,
    z numeric,
    bias_class text
)
LANGUAGE sql STABLE AS $$
WITH point_max AS (
    SELECT id AS point_system_id, max_score
    FROM point_system
),
user_shows AS (
    SELECT sh.id AS show_id
    FROM bias_vote_sets(p_include_revotes) vote_set
    JOIN show sh ON sh.id = vote_set.show_id
    WHERE vote_set.voter_id = p_user_id
      AND sh.status = 'full'
      AND sh.national_final_id IS NULL
      AND sh.year_id > 0
      AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
      AND (p_year_to IS NULL OR sh.year_id <= p_year_to)
    GROUP BY sh.id
),
scores AS (
    SELECT user_shows.show_id, member.language_id,
        COALESCE(SUM(vote.score) FILTER (WHERE vote_set.voter_id = p_user_id), 0) AS actual,
        COALESCE(SUM(vote.score) FILTER (WHERE vote_set.voter_id <> p_user_id), 0) AS others
    FROM user_shows
    JOIN bias_vote_sets(p_include_revotes) vote_set
      ON vote_set.show_id = user_shows.show_id
    JOIN vote ON vote.vote_set_id = vote_set.id
    JOIN song_show ON song_show.show_id = user_shows.show_id
                  AND song_show.song_id = vote.song_id
    JOIN current_song song ON song.id = vote.song_id
    JOIN language_set_language member
      ON member.language_set_id = song.language_set_id
    WHERE song.submitter_id <> p_user_id
    GROUP BY user_shows.show_id, member.language_id
),
pools AS (
    SELECT user_shows.show_id,
        COALESCE(SUM(vote.score) FILTER (WHERE vote_set.voter_id = p_user_id), 0) AS actual,
        COALESCE(SUM(vote.score) FILTER (WHERE vote_set.voter_id <> p_user_id), 0) AS others
    FROM user_shows
    JOIN bias_vote_sets(p_include_revotes) vote_set
      ON vote_set.show_id = user_shows.show_id
    JOIN vote ON vote.vote_set_id = vote_set.id
    JOIN song_show ON song_show.show_id = user_shows.show_id
                  AND song_show.song_id = vote.song_id
    JOIN current_song song ON song.id = vote.song_id
    WHERE song.submitter_id <> p_user_id
    GROUP BY user_shows.show_id
),
fields AS (
    SELECT user_shows.show_id, COUNT(DISTINCT song.id) AS entries
    FROM user_shows
    JOIN song_show ON song_show.show_id = user_shows.show_id
    JOIN current_song song ON song.id = song_show.song_id
    WHERE song.submitter_id <> p_user_id
    GROUP BY user_shows.show_id
),
aggregates AS (
    SELECT scores.language_id,
        SUM(scores.actual) AS actual,
        SUM(scores.others::numeric / NULLIF(pools.others, 0) * pools.actual) AS expected,
        SUM(fields.entries * scores.actual::numeric / NULLIF(pools.actual, 0)) AS weighted_actual,
        SUM(fields.entries * scores.others::numeric / NULLIF(pools.others, 0)) AS weighted_expected,
        SUM(fields.entries * fields.entries * scores.others::numeric
            / (NULLIF(pools.others, 0) * NULLIF(pools.actual, 0))) AS weighted_variance
    FROM scores
    JOIN pools USING (show_id)
    JOIN fields USING (show_id)
    WHERE pools.actual > 0
    GROUP BY scores.language_id
),
parts AS (
    SELECT member.language_id,
        COUNT(*) AS parts,
        COUNT(user_vote.id) AS votings_nonblank,
        COUNT(*) FILTER (WHERE user_vote.score = point_max.max_score) AS votings_max
    FROM user_shows
    JOIN song_show ON song_show.show_id = user_shows.show_id
    JOIN current_song song ON song.id = song_show.song_id
    JOIN language_set_language member
      ON member.language_set_id = song.language_set_id
    JOIN show sh ON sh.id = user_shows.show_id
    JOIN point_max ON point_max.point_system_id = sh.point_system_id
    LEFT JOIN bias_vote_sets(p_include_revotes) user_set
      ON user_set.show_id = user_shows.show_id
     AND user_set.voter_id = p_user_id
    LEFT JOIN vote user_vote
      ON user_vote.vote_set_id = user_set.id
     AND user_vote.song_id = song.id
    WHERE song.submitter_id <> p_user_id
    GROUP BY member.language_id
)
SELECT parts.language_id,
    language.name,
    parts.parts,
    parts.votings_nonblank,
    parts.votings_max,
    COALESCE(aggregates.actual, 0)::bigint,
    round(COALESCE(aggregates.expected, 0), 2),
    round(bias_ratio(COALESCE(aggregates.actual, 0), COALESCE(aggregates.expected, 0)), 4),
    round(bias_logratio(
        COALESCE(aggregates.weighted_actual, 0),
        COALESCE(aggregates.weighted_expected, 0)
    ), 4),
    round(bias_zw(
        COALESCE(aggregates.weighted_actual, 0),
        COALESCE(aggregates.weighted_expected, 0),
        COALESCE(aggregates.weighted_variance, 0)
    ), 3),
    bias_class(
        parts.parts,
        COALESCE(aggregates.weighted_actual, 0),
        COALESCE(aggregates.weighted_expected, 0),
        COALESCE(aggregates.expected, 0)
    )
FROM parts
LEFT JOIN aggregates USING (language_id)
JOIN language ON language.id = parts.language_id
ORDER BY bias_logratio(
    COALESCE(aggregates.weighted_actual, 0),
    COALESCE(aggregates.weighted_expected, 0)
) DESC, parts.parts DESC
$$;

CREATE OR REPLACE FUNCTION language_voter_bias(
    p_language_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_revotes boolean DEFAULT false
)
RETURNS TABLE (
    voter_id bigint,
    voter_name text,
    parts bigint,
    votings_nonblank bigint,
    votings_max bigint,
    given bigint,
    expected numeric,
    bias numeric,
    log_ratio numeric,
    z numeric,
    bias_class text
)
LANGUAGE sql STABLE AS $$
WITH point_max AS (
    SELECT id AS point_system_id, max_score
    FROM point_system
),
voter_shows AS (
    SELECT vote_set.voter_id, vote_set.show_id
    FROM bias_vote_sets(p_include_revotes) vote_set
    JOIN show sh ON sh.id = vote_set.show_id
    WHERE sh.status = 'full'
      AND sh.national_final_id IS NULL
      AND sh.year_id > 0
      AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
      AND (p_year_to IS NULL OR sh.year_id <= p_year_to)
),
scores AS (
    SELECT voter_shows.voter_id, voter_shows.show_id, member.language_id,
        COALESCE(SUM(vote.score) FILTER (
            WHERE source_set.voter_id = voter_shows.voter_id
        ), 0) AS actual,
        COALESCE(SUM(vote.score) FILTER (
            WHERE source_set.voter_id <> voter_shows.voter_id
        ), 0) AS others
    FROM voter_shows
    JOIN bias_vote_sets(p_include_revotes) source_set
      ON source_set.show_id = voter_shows.show_id
    JOIN vote ON vote.vote_set_id = source_set.id
    JOIN song_show ON song_show.show_id = voter_shows.show_id
                  AND song_show.song_id = vote.song_id
    JOIN current_song song ON song.id = vote.song_id
    JOIN language_set_language member
      ON member.language_set_id = song.language_set_id
    WHERE song.submitter_id <> voter_shows.voter_id
    GROUP BY voter_shows.voter_id, voter_shows.show_id, member.language_id
),
pools AS (
    SELECT voter_shows.voter_id, voter_shows.show_id,
        COALESCE(SUM(vote.score) FILTER (
            WHERE source_set.voter_id = voter_shows.voter_id
        ), 0) AS actual,
        COALESCE(SUM(vote.score) FILTER (
            WHERE source_set.voter_id <> voter_shows.voter_id
        ), 0) AS others
    FROM voter_shows
    JOIN bias_vote_sets(p_include_revotes) source_set
      ON source_set.show_id = voter_shows.show_id
    JOIN vote ON vote.vote_set_id = source_set.id
    JOIN song_show ON song_show.show_id = voter_shows.show_id
                  AND song_show.song_id = vote.song_id
    JOIN current_song song ON song.id = vote.song_id
    WHERE song.submitter_id <> voter_shows.voter_id
    GROUP BY voter_shows.voter_id, voter_shows.show_id
),
fields AS (
    SELECT voter_shows.voter_id, voter_shows.show_id,
        COUNT(DISTINCT song.id) AS entries
    FROM voter_shows
    JOIN song_show ON song_show.show_id = voter_shows.show_id
    JOIN current_song song ON song.id = song_show.song_id
    WHERE song.submitter_id <> voter_shows.voter_id
    GROUP BY voter_shows.voter_id, voter_shows.show_id
),
aggregates AS (
    SELECT scores.voter_id,
        SUM(scores.actual) AS actual,
        SUM(scores.others::numeric / NULLIF(pools.others, 0) * pools.actual) AS expected,
        SUM(fields.entries * scores.actual::numeric / NULLIF(pools.actual, 0)) AS weighted_actual,
        SUM(fields.entries * scores.others::numeric / NULLIF(pools.others, 0)) AS weighted_expected,
        SUM(fields.entries * fields.entries * scores.others::numeric
            / (NULLIF(pools.others, 0) * NULLIF(pools.actual, 0))) AS weighted_variance
    FROM scores
    JOIN pools USING (voter_id, show_id)
    JOIN fields USING (voter_id, show_id)
    WHERE scores.language_id = p_language_id
      AND pools.actual > 0
    GROUP BY scores.voter_id
),
parts AS (
    SELECT voter_shows.voter_id,
        COUNT(*) AS parts,
        COUNT(user_vote.id) AS votings_nonblank,
        COUNT(*) FILTER (WHERE user_vote.score = point_max.max_score) AS votings_max
    FROM voter_shows
    JOIN song_show ON song_show.show_id = voter_shows.show_id
    JOIN current_song song ON song.id = song_show.song_id
    JOIN language_set_language member
      ON member.language_set_id = song.language_set_id
     AND member.language_id = p_language_id
    JOIN show sh ON sh.id = voter_shows.show_id
    JOIN point_max ON point_max.point_system_id = sh.point_system_id
    LEFT JOIN bias_vote_sets(p_include_revotes) user_set
      ON user_set.show_id = voter_shows.show_id
     AND user_set.voter_id = voter_shows.voter_id
    LEFT JOIN vote user_vote
      ON user_vote.vote_set_id = user_set.id
     AND user_vote.song_id = song.id
    WHERE song.submitter_id <> voter_shows.voter_id
    GROUP BY voter_shows.voter_id
)
SELECT parts.voter_id,
    account.username,
    parts.parts,
    parts.votings_nonblank,
    parts.votings_max,
    COALESCE(aggregates.actual, 0)::bigint,
    round(COALESCE(aggregates.expected, 0), 2),
    round(bias_ratio(COALESCE(aggregates.actual, 0), COALESCE(aggregates.expected, 0)), 4),
    round(bias_logratio(
        COALESCE(aggregates.weighted_actual, 0),
        COALESCE(aggregates.weighted_expected, 0)
    ), 4),
    round(bias_zw(
        COALESCE(aggregates.weighted_actual, 0),
        COALESCE(aggregates.weighted_expected, 0),
        COALESCE(aggregates.weighted_variance, 0)
    ), 3),
    bias_class(
        parts.parts,
        COALESCE(aggregates.weighted_actual, 0),
        COALESCE(aggregates.weighted_expected, 0),
        COALESCE(aggregates.expected, 0)
    )
FROM parts
LEFT JOIN aggregates USING (voter_id)
JOIN account ON account.id = parts.voter_id
ORDER BY bias_logratio(
    COALESCE(aggregates.weighted_actual, 0),
    COALESCE(aggregates.weighted_expected, 0)
) DESC, parts.parts DESC
$$;

CREATE TRIGGER point_system_refresh
    AFTER UPDATE OF kind, metadata ON point_system
    FOR EACH ROW EXECUTE FUNCTION queue_point_system_refresh();

DROP FUNCTION IF EXISTS contest_ballot_entry_rule(bigint, text, bigint, text, bigint);
DROP FUNCTION IF EXISTS user_country_bias(bigint, bigint, bigint);
DROP FUNCTION IF EXISTS country_voter_bias(text, bigint, bigint);
DROP FUNCTION IF EXISTS user_submitter_bias(bigint, bigint, bigint, boolean);
DROP FUNCTION IF EXISTS submitter_voter_bias(bigint, bigint, bigint, boolean);

DROP TABLE point;
DROP FUNCTION trigger_queue_analytics_from_point();
ALTER TABLE point_system DROP COLUMN number;

COMMIT;
