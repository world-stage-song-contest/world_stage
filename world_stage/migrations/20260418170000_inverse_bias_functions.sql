BEGIN;

-- Inverse bias functions: the forward ones (user_country_bias,
-- user_submitter_bias) answer "for voter X, which targets are they biased
-- for/against?". These answer "for target X, which voters are biased
-- for/against them?".
--
-- Same formula, same CTE skeleton — just with the iteration key flipped.
-- For each voter v, we aggregate over v's qualifying shows and compute
-- p_c (population's allocation to the target in v's shows) and q_c
-- (v's allocation to the target), then the smoothed bias.

CREATE OR REPLACE FUNCTION country_voter_bias(p_country_id text)
RETURNS TABLE (
    voter_id bigint,
    voter_name text,
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
WITH voter_shows AS (
    SELECT vs.voter_id, vs.show_id
    FROM vote_set vs
    JOIN show sh ON sh.id = vs.show_id
    WHERE sh.status = 'full'
      AND sh.year_id > 0
),
songs_available AS (
    SELECT vss.voter_id, COUNT(*) AS songs_available
    FROM voter_shows vss
    JOIN song_show ss ON ss.show_id = vss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.country_id = p_country_id
      AND s.submitter_id <> vss.voter_id
    GROUP BY vss.voter_id
),
key_votes AS (
    SELECT vss.voter_id,
        COALESCE(SUM(v.score) FILTER (WHERE s.country_id = p_country_id), 0) AS total_given,
        SUM(v.score) AS total_given_all,
        COALESCE(SUM(v.score) FILTER (WHERE s.country_id = p_country_id
                                        AND inner_vs.voter_id = vss.voter_id), 0) AS user_given,
        COALESCE(SUM(v.score) FILTER (WHERE inner_vs.voter_id = vss.voter_id), 0) AS user_total_given
    FROM voter_shows vss
    JOIN vote_set inner_vs ON inner_vs.show_id = vss.show_id
    JOIN vote v ON v.vote_set_id = inner_vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id <> vss.voter_id
    GROUP BY vss.voter_id
),
show_user_points AS (
    SELECT vss.voter_id, vss.show_id, SUM(v.score) AS points_in_show
    FROM voter_shows vss
    JOIN vote_set vs ON vs.show_id = vss.show_id AND vs.voter_id = vss.voter_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id <> vss.voter_id
    GROUP BY vss.voter_id, vss.show_id
),
user_exposure AS (
    SELECT es.voter_id,
        COALESCE(SUM(sup.points_in_show), 0) AS exposure_points
    FROM (
        SELECT DISTINCT vss.voter_id, vss.show_id
        FROM voter_shows vss
        JOIN song_show ss ON ss.show_id = vss.show_id
        JOIN song s ON s.id = ss.song_id
        WHERE s.country_id = p_country_id
          AND s.submitter_id <> vss.voter_id
    ) es
    LEFT JOIN show_user_points sup
        ON sup.voter_id = es.voter_id AND sup.show_id = es.show_id
    GROUP BY es.voter_id
),
combined AS (
    SELECT COALESCE(sa.voter_id, kv.voter_id, ue.voter_id) AS voter_id,
        COALESCE(sa.songs_available, 0) AS songs_available,
        COALESCE(kv.user_given, 0) AS user_given,
        COALESCE(kv.total_given, 0) AS total_given,
        COALESCE(kv.total_given_all, 0) AS total_given_all,
        COALESCE(kv.user_total_given, 0) AS user_total_given,
        COALESCE(ue.exposure_points, 0) AS exposure_points
    FROM songs_available sa
    FULL OUTER JOIN key_votes kv ON kv.voter_id = sa.voter_id
    FULL OUTER JOIN user_exposure ue ON ue.voter_id = COALESCE(sa.voter_id, kv.voter_id)
),
bias_values AS (
    SELECT c.*,
        CASE WHEN c.user_total_given > 0
            THEN c.user_given::numeric / c.user_total_given
            ELSE 0 END AS q_c,
        CASE WHEN c.total_given_all > 0
            THEN c.total_given::numeric / c.total_given_all
            ELSE 0 END AS p_c
    FROM combined c
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
SELECT fb.voter_id,
    a.username AS voter_name,
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
LEFT JOIN account a ON a.id = fb.voter_id
WHERE fb.songs_available > 0
ORDER BY fb.bias DESC, fb.songs_available, fb.p_c
$$;


CREATE OR REPLACE FUNCTION submitter_voter_bias(p_submitter_id bigint)
RETURNS TABLE (
    voter_id bigint,
    voter_name text,
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
WITH voter_shows AS (
    SELECT vs.voter_id, vs.show_id
    FROM vote_set vs
    JOIN show sh ON sh.id = vs.show_id
    WHERE sh.status = 'full'
),
songs_available AS (
    SELECT vss.voter_id, COUNT(DISTINCT s.id) AS songs_available
    FROM voter_shows vss
    JOIN song_show ss ON ss.show_id = vss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id = p_submitter_id
      AND s.submitter_id <> vss.voter_id
    GROUP BY vss.voter_id
),
key_votes AS (
    SELECT vss.voter_id,
        COALESCE(SUM(v.score) FILTER (WHERE s.submitter_id = p_submitter_id), 0) AS total_given,
        SUM(v.score) AS total_given_all,
        COALESCE(SUM(v.score) FILTER (WHERE s.submitter_id = p_submitter_id
                                        AND inner_vs.voter_id = vss.voter_id), 0) AS user_given,
        COALESCE(SUM(v.score) FILTER (WHERE inner_vs.voter_id = vss.voter_id), 0) AS user_total_given
    FROM voter_shows vss
    JOIN vote_set inner_vs ON inner_vs.show_id = vss.show_id
    JOIN vote v ON v.vote_set_id = inner_vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id <> vss.voter_id
    GROUP BY vss.voter_id
),
show_user_points AS (
    SELECT vss.voter_id, vss.show_id, SUM(v.score) AS points_in_show
    FROM voter_shows vss
    JOIN vote_set vs ON vs.show_id = vss.show_id AND vs.voter_id = vss.voter_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id <> vss.voter_id
    GROUP BY vss.voter_id, vss.show_id
),
user_exposure AS (
    SELECT es.voter_id,
        COALESCE(SUM(sup.points_in_show), 0) AS exposure_points
    FROM (
        SELECT DISTINCT vss.voter_id, vss.show_id
        FROM voter_shows vss
        JOIN song_show ss ON ss.show_id = vss.show_id
        JOIN song s ON s.id = ss.song_id
        WHERE s.submitter_id = p_submitter_id
          AND s.submitter_id <> vss.voter_id
    ) es
    LEFT JOIN show_user_points sup
        ON sup.voter_id = es.voter_id AND sup.show_id = es.show_id
    GROUP BY es.voter_id
),
-- Reciprocal: for each voter v, how many points did the target submitter
-- give to v's songs? Group target's votes by the submitter of the target's
-- chosen songs.
reciprocal_from_target AS (
    SELECT s.submitter_id AS voter_id,
        SUM(v.score) AS pts_target_to_voter
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE vs.voter_id = p_submitter_id
      AND s.submitter_id <> p_submitter_id
      AND sh.status = 'full'
    GROUP BY s.submitter_id
),
combined AS (
    SELECT COALESCE(sa.voter_id, kv.voter_id, ue.voter_id) AS voter_id,
        COALESCE(sa.songs_available, 0) AS songs_available,
        COALESCE(kv.user_given, 0) AS user_given,
        COALESCE(kv.total_given, 0) AS total_given,
        COALESCE(kv.total_given_all, 0) AS total_given_all,
        COALESCE(kv.user_total_given, 0) AS user_total_given,
        COALESCE(ue.exposure_points, 0) AS exposure_points
    FROM songs_available sa
    FULL OUTER JOIN key_votes kv ON kv.voter_id = sa.voter_id
    FULL OUTER JOIN user_exposure ue ON ue.voter_id = COALESCE(sa.voter_id, kv.voter_id)
),
bias_values AS (
    SELECT c.*,
        CASE WHEN c.user_total_given > 0
            THEN c.user_given::numeric / c.user_total_given
            ELSE 0 END AS q_s,
        CASE WHEN c.total_given_all > 0
            THEN c.total_given::numeric / c.total_given_all
            ELSE 0 END AS p_s
    FROM combined c
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
        rft.pts_target_to_voter,
        CASE WHEN s.p_s > 0
            THEN (s.q_hat - s.p_s) / (s.p_s + 0.0005)
            ELSE 0 END AS bias
    FROM scored s
    LEFT JOIN reciprocal_from_target rft ON rft.voter_id = s.voter_id
)
SELECT fb.voter_id,
    a.username AS voter_name,
    fb.songs_available AS parts,
    fb.user_given,
    COALESCE(fb.pts_target_to_voter, 0) AS submitter_given,
    fb.total_given,
    (fb.user_given - COALESCE(fb.pts_target_to_voter, 0)) AS points_deficit,
    fb.user_total_given AS user_max,
    fb.total_given_all AS all_max,
    fb.q_s AS user_ratio,
    fb.p_s AS total_ratio,
    CASE WHEN fb.pts_target_to_voter > 0
        THEN (fb.user_given::numeric / fb.pts_target_to_voter) - 1
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
          OR fb.pts_target_to_voter IS NULL
          OR fb.pts_target_to_voter = 0 THEN 'inconclusive'
        WHEN (fb.user_given::numeric / fb.pts_target_to_voter) - 1 < -0.5 THEN 'very-negative'
        WHEN (fb.user_given::numeric / fb.pts_target_to_voter) - 1 < -0.1 THEN 'negative'
        WHEN (fb.user_given::numeric / fb.pts_target_to_voter) - 1 < 0.1 THEN 'neutral'
        WHEN (fb.user_given::numeric / fb.pts_target_to_voter) - 1 < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS reciprocal_bias_class
FROM final_bias fb
LEFT JOIN account a ON a.id = fb.voter_id
WHERE fb.songs_available > 0
ORDER BY fb.bias DESC, fb.songs_available, fb.p_s
$$;

COMMIT;
