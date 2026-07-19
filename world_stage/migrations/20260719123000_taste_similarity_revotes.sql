BEGIN;

-- Taste similarity uses each voter's latest ballot choice for a show by
-- default: a Revote ballot replaces the official ballot when present. Passing
-- false keeps the analysis strictly on official ballots.
DROP FUNCTION IF EXISTS user_taste_similarity(bigint, bigint, bigint, boolean);
DROP FUNCTION IF EXISTS user_taste_similarity(bigint, bigint, bigint, boolean, boolean);
CREATE FUNCTION user_taste_similarity(
    p_user_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_specials boolean DEFAULT true,
    p_include_revotes boolean DEFAULT true
)
RETURNS TABLE (
    other_id bigint,
    other_name text,
    shared_shows bigint,
    co_voted_songs bigint,
    similarity numeric,
    similarity_class text
)
LANGUAGE sql STABLE AS $$
WITH target_shows AS (
    SELECT vs.id AS vote_set_id, vs.show_id
    FROM bias_vote_sets(p_include_revotes) vs
    JOIN show sh ON sh.id = vs.show_id
    WHERE vs.voter_id = p_user_id
      AND sh.status = 'full'
      AND (
          (sh.year_id > 0
           AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
           AND (p_year_to IS NULL OR sh.year_id <= p_year_to))
          OR (p_include_specials AND sh.year_id < 0)
      )
),
show_songs AS (
    SELECT ts.show_id, s.id AS song_id, s.submitter_id
    FROM target_shows ts
    JOIN song_show ss ON ss.show_id = ts.show_id
    JOIN song s ON s.id = ss.song_id
),
target_votes AS (
    SELECT ts.show_id, v.song_id, v.score
    FROM target_shows ts
    JOIN vote v ON v.vote_set_id = ts.vote_set_id
),
other_sets AS (
    SELECT vs.id AS vote_set_id, vs.show_id, vs.voter_id
    FROM bias_vote_sets(p_include_revotes) vs
    JOIN target_shows ts ON ts.show_id = vs.show_id
    WHERE vs.voter_id <> p_user_id
),
other_votes AS (
    SELECT os.voter_id, os.show_id, v.song_id, v.score
    FROM other_sets os
    JOIN vote v ON v.vote_set_id = os.vote_set_id
),
pairs AS (
    SELECT os.voter_id, sh.show_id, sh.song_id,
        tv.score AS t_score,
        ov.score AS u_score
    FROM other_sets os
    JOIN show_songs sh ON sh.show_id = os.show_id
    JOIN target_votes tv
        ON tv.show_id = sh.show_id AND tv.song_id = sh.song_id
    JOIN other_votes ov
        ON ov.voter_id = os.voter_id AND ov.show_id = sh.show_id
       AND ov.song_id = sh.song_id
    WHERE sh.submitter_id IS DISTINCT FROM p_user_id
      AND sh.submitter_id IS DISTINCT FROM os.voter_id
),
per_show AS (
    SELECT voter_id, show_id,
        COUNT(*)::numeric AS n,
        SUM(t_score)::numeric AS sum_t,
        SUM(u_score)::numeric AS sum_u,
        SUM(t_score::numeric * u_score) AS sum_tu,
        SUM(t_score::numeric * t_score) AS sum_tt,
        SUM(u_score::numeric * u_score) AS sum_uu
    FROM pairs
    GROUP BY voter_id, show_id
),
agg AS (
    SELECT voter_id,
        COUNT(*) AS shared_shows,
        SUM(n)::bigint AS co_voted_songs,
        SUM(sum_tu - sum_t * sum_u / n) AS cov,
        SUM(sum_tt - sum_t * sum_t / n) AS var_t,
        SUM(sum_uu - sum_u * sum_u / n) AS var_u
    FROM per_show
    GROUP BY voter_id
),
scored AS (
    SELECT a.*,
        CASE WHEN a.var_t > 0 AND a.var_u > 0
            THEN a.cov / sqrt(a.var_t * a.var_u)
            ELSE NULL END AS similarity
    FROM agg a
)
SELECT s.voter_id AS other_id,
    acc.username AS other_name,
    s.shared_shows,
    s.co_voted_songs,
    s.similarity,
    CASE
        WHEN s.shared_shows < 5 OR s.similarity IS NULL THEN 'inconclusive'
        WHEN s.similarity >= 0.20 THEN 'very-positive'
        WHEN s.similarity >= 0.075 THEN 'positive'
        WHEN s.similarity > -0.05 THEN 'neutral'
        WHEN s.similarity > -0.20 THEN 'negative'
        ELSE 'very-negative'
    END AS similarity_class
FROM scored s
JOIN account acc ON acc.id = s.voter_id
ORDER BY s.similarity DESC NULLS LAST, s.shared_shows DESC
$$;

COMMIT;
