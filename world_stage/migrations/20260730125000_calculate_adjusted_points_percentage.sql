-- Calculate the midpoint-based percentage alongside the legacy percentage.

CREATE OR REPLACE FUNCTION calculate_adjusted_points_percentage(
    p_points numeric,
    p_midpoint numeric,
    p_max_possible numeric
)
RETURNS numeric AS $$
BEGIN
    IF p_midpoint IS NULL OR p_midpoint <= 0 THEN
        RETURN 0;
    END IF;
    IF p_points <= p_midpoint THEN
        RETURN 50 * p_points / p_midpoint;
    END IF;
    IF p_max_possible <= p_midpoint THEN
        RAISE EXCEPTION
            'Adjusted maximum % must exceed midpoint % for points %',
            p_max_possible, p_midpoint, p_points;
    END IF;
    RETURN 50 + 50 * (p_points - p_midpoint)
        / (p_max_possible - p_midpoint);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION refresh_show_results_for_mode(
    p_show_id bigint, p_result_mode text
)
RETURNS void AS $$
DECLARE
    v_max_point integer;
    v_system_sum bigint;
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

    DELETE FROM country_show_results
    WHERE show_id = p_show_id AND result_mode = p_result_mode;

    WITH si AS (
        SELECT
            sh.id, sh.year_id, sh.dtf, sh.sc, sh.special,
            sh.short_name, sh.show_name
        FROM show sh
        WHERE sh.id = p_show_id
    ),
    all_vote_sets AS (
        SELECT * FROM effective_show_vote_sets(p_show_id, p_result_mode)
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
        SELECT s.id AS song_id, s.country_id, v.score, COUNT(*) AS cnt
        FROM si
        JOIN song_show ss ON ss.show_id = si.id
        JOIN song s ON s.id = ss.song_id
        JOIN vote v ON v.song_id = s.id
        JOIN all_vote_sets vs ON vs.id = v.vote_set_id
        JOIN LATERAL ballot_entry_rule(
            si.id, p_result_mode, vs.voter_id, vs.country_id, s.id
        ) rule ON true
        WHERE rule.rule_kind <> 'FORBIDDEN'
          AND (
              rule.rule_kind <> 'FORCED'
              OR v.score = rule.required_score
          )
        GROUP BY s.id, s.country_id, v.score
    ),
    entry_caps AS (
        SELECT s.id AS song_id, SUM(rule.score_cap)::integer AS score_cap
        FROM si
        JOIN song_show ss ON ss.show_id = si.id
        JOIN song s ON s.id = ss.song_id
        JOIN country c ON c.id = s.country_id
        CROSS JOIN all_vote_sets vs
        CROSS JOIN LATERAL ballot_entry_rule(
            si.id, p_result_mode, vs.voter_id, vs.country_id, s.id
        ) rule
        WHERE c.is_participating
        GROUP BY s.id
    ),
    aggregated AS (
        SELECT
            s.country_id,
            c.name AS country_name,
            si.id AS show_id,
            s.id AS song_id,
            ss.running_order,
            si.show_name,
            si.short_name,
            si.year_id,
            si.dtf,
            si.sc,
            si.special,
            GREATEST(
                COALESCE(SUM(pc.score * pc.cnt), 0)
                - COALESCE(
                    CASE
                        WHEN p_result_mode = 'revote' THEN ss.revote_penalty
                        ELSE ss.penalty
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
        JOIN song_show ss ON ss.show_id = si.id
        JOIN song s ON s.id = ss.song_id
        JOIN country c ON c.id = s.country_id
        LEFT JOIN point_counts pc ON pc.song_id = s.id
        LEFT JOIN voters_by_country vbc ON vbc.country_id = s.country_id
        LEFT JOIN entry_caps ec ON ec.song_id = s.id
        WHERE c.is_participating
        GROUP BY
            s.country_id, c.name, si.id, s.id, ss.running_order,
            ss.penalty, ss.revote_penalty, si.show_name, si.short_name,
            si.year_id, si.dtf, si.sc, si.special, vbc.cnt, ec.score_cap
    ),
    ranked AS (
        SELECT
            a.*,
            DENSE_RANK() OVER (
                ORDER BY
                    total_points DESC,
                    total_votes_received DESC,
                    countback_string DESC
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
        adjusted_max_possible_points, points_midpoint, entry_status, max_pts,
        total_voters, result_mode
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
        CASE
            WHEN n.short_name = 'f' THEN NULL
            WHEN n.place <= COALESCE(n.dtf, 0) THEN 'dtf'
            WHEN n.place <= COALESCE(n.dtf, 0) + COALESCE(n.special, 0)
                THEN 'special'
            WHEN n.place <= COALESCE(n.dtf, 0) + COALESCE(n.special, 0)
                    + COALESCE(n.sc, 0)
                THEN 'sc'
            ELSE 'nq'
        END,
        v_max_point,
        n.total_voters,
        p_result_mode
    FROM normalized n;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Refreshed % results for show %; rows=%',
        p_result_mode, p_show_id, v_inserted;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    show_row record;
BEGIN
    FOR show_row IN SELECT id FROM show LOOP
        PERFORM refresh_show_results_for_mode(show_row.id, 'official');
    END LOOP;

    FOR show_row IN
        SELECT DISTINCT show_id
        FROM (
            SELECT show_id
            FROM vote_set
            WHERE result_mode = 'revote'
            UNION
            SELECT show_id
            FROM country_show_results
            WHERE result_mode = 'revote'
        ) revote_shows
    LOOP
        PERFORM refresh_show_results_for_mode(show_row.show_id, 'revote');
    END LOOP;
END;
$$;

ALTER TABLE country_show_results
    ALTER COLUMN adjusted_points_percentage SET NOT NULL,
    ALTER COLUMN adjusted_max_possible_points SET NOT NULL,
    ALTER COLUMN points_midpoint SET NOT NULL;
