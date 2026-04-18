BEGIN;

-- Move the bias-page queries out of Python. They're parameterized by user_id
-- so a plain view doesn't fit; set-returning SQL functions give us the same
-- call shape (SELECT * FROM fn(%s)) while keeping the query logic in the DB.
--
-- Optimized vs. the original Python inline versions:
--   * all_votes + user_votes merged via FILTER (one scan instead of two).
--   * all_totals + user_total merged (one scan instead of two).
--   * Redundant re-joins/re-filters of `show` dropped — user_shows already
--     restricts to status='full' (and year_id > 0 for the country variant,
--     which excludes specials), so re-filtering downstream was pointless
--     work.
--   * combined is a 3-way FULL OUTER JOIN instead of 4-way.

CREATE OR REPLACE FUNCTION user_country_bias(p_user_id bigint)
RETURNS TABLE (
    country_id text,
    country_name text,
    parts bigint,
    user_given bigint,
    user_max numeric,
    user_ratio numeric,
    total_given bigint,
    total_max numeric,
    total_ratio numeric,
    bias numeric,
    bias_class text
)
LANGUAGE sql STABLE AS $$
WITH user_shows AS (
    SELECT s.id AS show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE vs.voter_id = p_user_id
      AND s.status = 'full'
      AND s.year_id > 0
    GROUP BY s.id
),
songs_available AS (
    SELECT s.country_id, COUNT(*) AS songs_available
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> p_user_id
    GROUP BY s.country_id
),
key_votes AS (
    SELECT s.country_id,
        SUM(v.score) AS total_given,
        COALESCE(SUM(v.score) FILTER (WHERE vs.voter_id = p_user_id), 0) AS user_given,
        COUNT(*) FILTER (WHERE vs.voter_id = p_user_id) AS user_votes
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id <> p_user_id
    GROUP BY s.country_id
),
totals AS (
    SELECT COALESCE(SUM(total_given), 0) AS total_given_all,
        COALESCE(SUM(user_given), 0) AS user_total_given
    FROM key_votes
),
show_user_points AS (
    SELECT us.show_id, SUM(v.score) AS user_points_in_show
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id AND vs.voter_id = p_user_id
    JOIN vote v ON v.vote_set_id = vs.id
    GROUP BY us.show_id
),
user_exposure AS (
    SELECT cs.country_id,
        COALESCE(SUM(sup.user_points_in_show), 0) AS exposure_points
    FROM (
        SELECT DISTINCT us.show_id, s.country_id
        FROM user_shows us
        JOIN song_show ss ON ss.show_id = us.show_id
        JOIN song s ON s.id = ss.song_id
        WHERE s.submitter_id <> p_user_id
    ) cs
    LEFT JOIN show_user_points sup ON sup.show_id = cs.show_id
    GROUP BY cs.country_id
),
combined AS (
    SELECT COALESCE(sa.country_id, kv.country_id, ue.country_id) AS country_id,
        COALESCE(sa.songs_available, 0) AS songs_available,
        COALESCE(kv.user_given, 0) AS user_given,
        COALESCE(kv.user_votes, 0) AS user_votes,
        COALESCE(kv.total_given, 0) AS total_given,
        COALESCE(ue.exposure_points, 0) AS exposure_points
    FROM songs_available sa
    FULL OUTER JOIN key_votes kv ON kv.country_id = sa.country_id
    FULL OUTER JOIN user_exposure ue ON ue.country_id = COALESCE(sa.country_id, kv.country_id)
),
bias_values AS (
    SELECT c.*,
        t.user_total_given::numeric AS user_total_given,
        t.total_given_all::numeric AS total_given_all,
        CASE WHEN t.user_total_given > 0
            THEN c.user_given::numeric / t.user_total_given
            ELSE 0 END AS q_c,
        CASE WHEN t.total_given_all > 0
            THEN c.total_given::numeric / t.total_given_all
            ELSE 0 END AS p_c
    FROM combined c
    CROSS JOIN totals t
),
scored AS (
    SELECT bv.*,
        bv.exposure_points::numeric AS n_c,
        CASE WHEN bv.p_c > 0 AND bv.exposure_points > 0
            THEN (bv.exposure_points / (bv.exposure_points + 100.0)) * bv.q_c
               + (100.0 / (bv.exposure_points + 100.0)) * bv.p_c
            WHEN bv.p_c > 0 THEN bv.p_c
            ELSE 0 END AS q_hat
    FROM bias_values bv
),
final_bias AS (
    SELECT s.*,
        CASE WHEN s.p_c > 0
            THEN (s.q_hat - s.p_c) / (s.p_c + 0.0005)
            ELSE 0 END AS bias
    FROM scored s
)
SELECT fb.country_id,
    c.name AS country_name,
    fb.songs_available AS parts,
    fb.user_given,
    fb.user_total_given AS user_max,
    fb.q_c AS user_ratio,
    fb.total_given,
    fb.total_given_all AS total_max,
    fb.p_c AS total_ratio,
    fb.bias,
    CASE
        WHEN fb.songs_available < 5 OR fb.n_c < 50 THEN 'inconclusive'
        WHEN fb.bias < -0.5 THEN 'very-negative'
        WHEN fb.bias < -0.1 THEN 'negative'
        WHEN fb.bias < 0.1 THEN 'neutral'
        WHEN fb.bias < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS bias_class
FROM final_bias fb
LEFT JOIN country c ON c.id = fb.country_id
WHERE fb.songs_available > 0
ORDER BY fb.bias DESC, fb.songs_available, fb.p_c
$$;


CREATE OR REPLACE FUNCTION user_submitter_bias(p_user_id bigint)
RETURNS TABLE (
    submitter_id bigint,
    submitter_name text,
    parts bigint,
    user_given bigint,
    submitter_given bigint,
    total_given bigint,
    points_deficit bigint,
    user_max numeric,
    all_max numeric,
    user_ratio numeric,
    total_ratio numeric,
    reciprocal_bias numeric,
    bias numeric,
    bias_class text,
    reciprocal_bias_class text
)
LANGUAGE sql STABLE AS $$
WITH user_shows AS (
    SELECT s.id AS show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE vs.voter_id = p_user_id
      AND s.status = 'full'
    GROUP BY s.id
),
songs_available AS (
    SELECT s.submitter_id, COUNT(DISTINCT s.id) AS songs_available
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> p_user_id
    GROUP BY s.submitter_id
),
key_votes AS (
    SELECT s.submitter_id,
        SUM(v.score) AS total_given,
        COALESCE(SUM(v.score) FILTER (WHERE vs.voter_id = p_user_id), 0) AS user_given,
        COUNT(*) FILTER (WHERE vs.voter_id = p_user_id) AS user_votes
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id <> p_user_id
    GROUP BY s.submitter_id
),
totals AS (
    SELECT COALESCE(SUM(total_given), 0) AS total_given_all,
        COALESCE(SUM(user_given), 0) AS user_total_given
    FROM key_votes
),
show_user_points AS (
    SELECT us.show_id, SUM(v.score) AS user_points_in_show
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id AND vs.voter_id = p_user_id
    JOIN vote v ON v.vote_set_id = vs.id
    GROUP BY us.show_id
),
user_exposure AS (
    SELECT ss.submitter_id,
        COALESCE(SUM(sup.user_points_in_show), 0) AS exposure_points
    FROM (
        SELECT DISTINCT us.show_id, s.submitter_id
        FROM user_shows us
        JOIN song_show ss ON ss.show_id = us.show_id
        JOIN song s ON s.id = ss.song_id
        WHERE s.submitter_id <> p_user_id
    ) ss
    LEFT JOIN show_user_points sup ON sup.show_id = ss.show_id
    GROUP BY ss.submitter_id
),
reciprocal_points AS (
    SELECT vs.voter_id AS submitter_id,
        SUM(v.score) AS pts_target_to_user
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE s.submitter_id = p_user_id
      AND vs.voter_id <> p_user_id
      AND sh.status = 'full'
    GROUP BY vs.voter_id
),
combined AS (
    SELECT COALESCE(sa.submitter_id, kv.submitter_id, ue.submitter_id) AS submitter_id,
        COALESCE(sa.songs_available, 0) AS songs_available,
        COALESCE(kv.user_given, 0) AS user_given,
        COALESCE(kv.user_votes, 0) AS user_votes,
        COALESCE(kv.total_given, 0) AS total_given,
        COALESCE(ue.exposure_points, 0) AS exposure_points
    FROM songs_available sa
    FULL OUTER JOIN key_votes kv ON kv.submitter_id = sa.submitter_id
    FULL OUTER JOIN user_exposure ue ON ue.submitter_id = COALESCE(sa.submitter_id, kv.submitter_id)
),
bias_values AS (
    SELECT c.*,
        t.user_total_given::numeric AS user_total_given,
        t.total_given_all::numeric AS total_given_all,
        CASE WHEN t.user_total_given > 0
            THEN c.user_given::numeric / t.user_total_given
            ELSE 0 END AS q_s,
        CASE WHEN t.total_given_all > 0
            THEN c.total_given::numeric / t.total_given_all
            ELSE 0 END AS p_s
    FROM combined c
    CROSS JOIN totals t
),
scored AS (
    SELECT bv.*,
        bv.exposure_points::numeric AS n_s,
        CASE WHEN bv.p_s > 0 AND bv.exposure_points > 0
            THEN (bv.exposure_points / (bv.exposure_points + 100.0)) * bv.q_s
               + (100.0 / (bv.exposure_points + 100.0)) * bv.p_s
            WHEN bv.p_s > 0 THEN bv.p_s
            ELSE 0 END AS q_hat
    FROM bias_values bv
),
final_bias AS (
    SELECT s.*,
        rp.pts_target_to_user,
        CASE WHEN s.p_s > 0
            THEN (s.q_hat - s.p_s) / (s.p_s + 0.0005)
            ELSE 0 END AS bias
    FROM scored s
    LEFT JOIN reciprocal_points rp ON rp.submitter_id = s.submitter_id
)
SELECT fb.submitter_id,
    a.username AS submitter_name,
    fb.songs_available AS parts,
    fb.user_given,
    COALESCE(fb.pts_target_to_user, 0) AS submitter_given,
    fb.total_given,
    (fb.user_given - COALESCE(fb.pts_target_to_user, 0)) AS points_deficit,
    fb.user_total_given AS user_max,
    fb.total_given_all AS all_max,
    fb.q_s AS user_ratio,
    fb.p_s AS total_ratio,
    CASE WHEN fb.pts_target_to_user > 0
        THEN (fb.user_given::numeric / fb.pts_target_to_user) - 1
        ELSE 0 END AS reciprocal_bias,
    fb.bias,
    CASE
        WHEN fb.songs_available < 5 OR fb.n_s < 50 THEN 'inconclusive'
        WHEN fb.bias < -0.5 THEN 'very-negative'
        WHEN fb.bias < -0.1 THEN 'negative'
        WHEN fb.bias < 0.1 THEN 'neutral'
        WHEN fb.bias < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS bias_class,
    CASE
        WHEN fb.songs_available < 5
          OR fb.pts_target_to_user IS NULL
          OR fb.pts_target_to_user = 0 THEN 'inconclusive'
        WHEN (fb.user_given::numeric / fb.pts_target_to_user) - 1 < -0.5 THEN 'very-negative'
        WHEN (fb.user_given::numeric / fb.pts_target_to_user) - 1 < -0.1 THEN 'negative'
        WHEN (fb.user_given::numeric / fb.pts_target_to_user) - 1 < 0.1 THEN 'neutral'
        WHEN (fb.user_given::numeric / fb.pts_target_to_user) - 1 < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS reciprocal_bias_class
FROM final_bias fb
LEFT JOIN account a ON a.id = fb.submitter_id
WHERE fb.songs_available > 0
ORDER BY fb.bias DESC, fb.songs_available, fb.p_s
$$;

COMMIT;
