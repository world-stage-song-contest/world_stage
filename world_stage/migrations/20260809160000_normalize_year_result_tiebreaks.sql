-- Rank entries within the deepest round they reached using values normalized
-- for jury size: share of available points, share of voters awarding points,
-- then the share of voters awarding each score from highest to lowest.
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
    score_values AS (
        SELECT GENERATE_SERIES(COALESCE(MAX(max_pts), 0), 1, -1) AS score
        FROM country_show_results
        WHERE year_id = p_year_id AND result_mode = 'official'
    ),
    candidates AS (
        SELECT csr.*, show_tiers.tier, show_tiers.show_number,
               ROW_NUMBER() OVER (
                   PARTITION BY csr.song_id
                   ORDER BY show_tiers.tier, csr.place, csr.show_id
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
        JOIN show_tiers ON show_tiers.show_id = csr.show_id
        WHERE csr.year_id = p_year_id AND csr.result_mode = 'official'
    ),
    reached AS (
        SELECT * FROM candidates WHERE reached_order = 1
    ),
    all_ranked AS (
        SELECT country_id, country_name, year_id, song_id,
               ROW_NUMBER() OVER (
                   ORDER BY tier, points_share DESC NULLS LAST,
                            voter_share DESC NULLS LAST,
                            countback DESC NULLS LAST,
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
                3
            )
        END AS placement_percentage
    FROM all_ranked ar
    CROSS JOIN totals t
    ORDER BY ar.place;
END;
$$;

-- Correct cached rankings for historical closed years as part of deployment.
SELECT refresh_year_results(id)
FROM year
WHERE status = 'closed';
