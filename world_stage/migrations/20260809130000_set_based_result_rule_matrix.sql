-- Build each show's complete ballot-entry rule matrix once per result refresh.
-- This replaces two nested scalar ballot_entry_rule calls per ballot-entry pair.
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
    v_scored_positions integer;
    v_national_final_id bigint;
BEGIN
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;

    SELECT
        sh.voting_ruleset_version,
        sh.revote_ruleset_version,
        MAX(p.score),
        COUNT(p.id),
        sh.national_final_id
    INTO v_ruleset, v_revote_ruleset, v_max_score, v_scored_positions,
         v_national_final_id
    FROM show sh
    JOIN point p ON p.point_system_id = sh.point_system_id
    WHERE sh.id = p_show_id
    GROUP BY sh.voting_ruleset_version, sh.revote_ruleset_version,
             sh.national_final_id;

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
            SELECT revision.submitter_id, revision.title, revision.artist
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
        ) data ON data.title IS NOT NULL AND data.artist IS NOT NULL
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
                THEN 1
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
                THEN 1
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
           SELECT 1 FROM point p
           JOIN show sh ON sh.point_system_id = p.point_system_id
           WHERE sh.id = p_show_id AND p.score = 1
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

    SELECT MAX(p.score), COALESCE(SUM(p.score), 0)
    INTO v_max_point, v_system_sum
    FROM show sh
    JOIN point p ON p.point_system_id = sh.point_system_id
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
            SELECT revision.submitter_id, revision.title, revision.artist
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
        ) data ON data.title IS NOT NULL AND data.artist IS NOT NULL
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
                    LPAD(pc.score::text, 3, '0') || ':'
                        || LPAD(pc.cnt::text, 3, '0'),
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
