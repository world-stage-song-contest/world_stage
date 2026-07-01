-- Per-show bias measure (merged rework of the bias functions).
--
-- Squashes the 20260701 dev iterations (per-show rewrite, effect-size class,
-- voting counts, field weighting) into one migration so downstream databases
-- get the final state directly. Self-contained and idempotent: drops any prior
-- versions of the functions, then recreates helpers and the four bias
-- functions.
--
-- Measure: for each (voter, target, show) the actual points are compared with
-- an expected value built from the other voters' consensus share, scaled to the
-- voter's pool. Each show is weighted by its votable field size (N / pool), so a
-- point in a deep field counts for more. Bias is the smoothed log-ratio of the
-- weighted totals; z is the weighted standardised deviation; Given/Expected and
-- the raw Ratio stay in plain points.

DROP FUNCTION IF EXISTS user_country_bias(bigint, bigint, bigint);
DROP FUNCTION IF EXISTS user_submitter_bias(bigint, bigint, bigint, boolean);
DROP FUNCTION IF EXISTS country_voter_bias(text, bigint, bigint);
DROP FUNCTION IF EXISTS submitter_voter_bias(bigint, bigint, bigint, boolean);
DROP FUNCTION IF EXISTS bias_class(bigint, numeric, numeric);

CREATE OR REPLACE FUNCTION bias_logratio(a numeric, e numeric) RETURNS numeric
    IMMUTABLE LANGUAGE sql AS $$ SELECT ln((a + 2) / (e + 2)) $$;

CREATE OR REPLACE FUNCTION bias_z(a numeric, e numeric) RETURNS numeric
    IMMUTABLE LANGUAGE sql AS $$ SELECT CASE WHEN e > 0 THEN (a - e) / sqrt(e) ELSE 0 END $$;

-- Weighted z-score: deviation over the weighted standard deviation.
CREATE OR REPLACE FUNCTION bias_zw(a numeric, e numeric, var numeric) RETURNS numeric
    IMMUTABLE LANGUAGE sql AS $$ SELECT CASE WHEN var > 0 THEN (a - e) / sqrt(var) ELSE 0 END $$;

CREATE OR REPLACE FUNCTION bias_ratio(a numeric, e numeric) RETURNS numeric
    IMMUTABLE LANGUAGE sql AS $$ SELECT CASE WHEN e > 0 THEN a / e - 1 ELSE 0 END $$;

-- effect size (field-weighted log_ratio) + exposure gate (parts, raw E)
CREATE OR REPLACE FUNCTION bias_class(parts bigint, a numeric, e numeric, e_raw numeric) RETURNS text
    IMMUTABLE LANGUAGE sql AS $$
    SELECT CASE
        WHEN parts < 5 OR e_raw < 10 THEN 'inconclusive'
        WHEN abs(bias_logratio(a, e)) < 0.18 THEN 'neutral'
        WHEN bias_logratio(a, e) >= 0.51 THEN 'very-positive'
        WHEN bias_logratio(a, e) <= -0.51 THEN 'very-negative'
        WHEN bias_logratio(a, e) > 0 THEN 'positive'
        ELSE 'negative'
    END
    $$;

-- Reciprocal class keeps the simple ratio bucketing (you-to-them vs them-to-you):
-- ``val`` is user_given/target_to_user - 1; inconclusive when thin or one-sided.
CREATE OR REPLACE FUNCTION bias_reciprocal_class(parts bigint, mutual numeric, val numeric)
    RETURNS text IMMUTABLE LANGUAGE sql AS $$
    SELECT CASE
        WHEN parts < 5 OR mutual IS NULL OR mutual = 0 THEN 'inconclusive'
        WHEN val < -0.5 THEN 'very-negative'
        WHEN val < -0.1 THEN 'negative'
        WHEN val < 0.1 THEN 'neutral'
        WHEN val < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END
    $$;

-- ── Forward: for one voter, which countries are they biased toward? ──

CREATE OR REPLACE FUNCTION user_country_bias(
    p_user_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL
)
RETURNS TABLE (
    country_id text,
    country_name text,
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
    SELECT point_system_id, MAX(score) AS max_score FROM point GROUP BY point_system_id
),
user_shows AS (
    SELECT s.id AS show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE vs.voter_id = p_user_id
      AND s.status = 'full'
      AND s.year_id > 0
      AND (p_year_from IS NULL OR s.year_id >= p_year_from)
      AND (p_year_to IS NULL OR s.year_id <= p_year_to)
    GROUP BY s.id
),
sc AS (
    SELECT us.show_id, sg.country_id,
        COALESCE(SUM(v.score) FILTER (WHERE vs.voter_id = p_user_id), 0) AS actual,
        COALESCE(SUM(v.score) FILTER (WHERE vs.voter_id <> p_user_id), 0) AS others_t
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song sg ON sg.id = v.song_id
    WHERE sg.submitter_id <> p_user_id
    GROUP BY us.show_id, sg.country_id
),
pool AS (
    SELECT show_id, SUM(actual) AS pool_v, SUM(others_t) AS others_all
    FROM sc GROUP BY show_id
),
vfield AS (
    SELECT us.show_id, COUNT(DISTINCT sg.id) AS n
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song sg ON sg.id = ss.song_id
    WHERE sg.submitter_id <> p_user_id
    GROUP BY us.show_id
),
agg AS (
    SELECT sc.country_id,
        SUM(sc.actual) AS a_raw,
        SUM(sc.others_t::numeric / NULLIF(p.others_all, 0) * p.pool_v) AS e_raw,
        SUM(f.n * sc.actual::numeric / NULLIF(p.pool_v, 0)) AS a_w,
        SUM(f.n * sc.others_t::numeric / NULLIF(p.others_all, 0)) AS e_w,
        SUM(f.n * f.n * sc.others_t::numeric / (NULLIF(p.others_all, 0) * NULLIF(p.pool_v, 0))) AS v_w
    FROM sc
    JOIN pool p ON p.show_id = sc.show_id
    JOIN vfield f ON f.show_id = sc.show_id
    WHERE p.pool_v > 0
    GROUP BY sc.country_id
),
parts AS (
    SELECT sg.country_id,
        COUNT(*) AS parts,
        COUNT(uv.id) AS votings_nonblank,
        COUNT(*) FILTER (WHERE uv.score = pm.max_score) AS votings_max
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song sg ON sg.id = ss.song_id
    JOIN show sh ON sh.id = us.show_id
    JOIN point_max pm ON pm.point_system_id = sh.point_system_id
    LEFT JOIN vote_set uvs ON uvs.show_id = us.show_id AND uvs.voter_id = p_user_id
    LEFT JOIN vote uv ON uv.vote_set_id = uvs.id AND uv.song_id = sg.id
    WHERE sg.submitter_id <> p_user_id
    GROUP BY sg.country_id
)
SELECT pt.country_id,
    c.name AS country_name,
    pt.parts, pt.votings_nonblank, pt.votings_max,
    COALESCE(ag.a_raw, 0)::bigint AS given,
    round(COALESCE(ag.e_raw, 0), 2) AS expected,
    round(bias_ratio(COALESCE(ag.a_raw, 0), COALESCE(ag.e_raw, 0)), 4) AS bias,
    round(bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)), 4) AS log_ratio,
    round(bias_zw(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.v_w, 0)), 3) AS z,
    bias_class(pt.parts, COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.e_raw, 0)) AS bias_class
FROM parts pt
LEFT JOIN agg ag ON ag.country_id = pt.country_id
LEFT JOIN country c ON c.id = pt.country_id
ORDER BY bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)) DESC, pt.parts DESC
$$;

CREATE OR REPLACE FUNCTION user_submitter_bias(
    p_user_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_specials boolean DEFAULT true
)
RETURNS TABLE (
    submitter_id bigint,
    submitter_name text,
    parts bigint,
    votings_nonblank bigint,
    votings_max bigint,
    given bigint,
    expected numeric,
    bias numeric,
    log_ratio numeric,
    z numeric,
    bias_class text,
    received bigint,
    deficit bigint,
    received_any bigint,
    received_max bigint,
    reciprocal_bias numeric,
    reciprocal_bias_class text
)
LANGUAGE sql STABLE AS $$
WITH point_max AS (
    SELECT point_system_id, MAX(score) AS max_score FROM point GROUP BY point_system_id
),
user_shows AS (
    SELECT s.id AS show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE vs.voter_id = p_user_id
      AND s.status = 'full'
      AND (
          (s.year_id > 0
           AND (p_year_from IS NULL OR s.year_id >= p_year_from)
           AND (p_year_to IS NULL OR s.year_id <= p_year_to))
          OR (p_include_specials AND s.year_id < 0)
      )
    GROUP BY s.id
),
sc AS (
    SELECT us.show_id, sg.submitter_id,
        COALESCE(SUM(v.score) FILTER (WHERE vs.voter_id = p_user_id), 0) AS actual,
        COALESCE(SUM(v.score) FILTER (WHERE vs.voter_id <> p_user_id), 0) AS others_t
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song sg ON sg.id = v.song_id
    WHERE sg.submitter_id <> p_user_id
    GROUP BY us.show_id, sg.submitter_id
),
pool AS (
    SELECT show_id, SUM(actual) AS pool_v, SUM(others_t) AS others_all
    FROM sc GROUP BY show_id
),
vfield AS (
    SELECT us.show_id, COUNT(DISTINCT sg.id) AS n
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song sg ON sg.id = ss.song_id
    WHERE sg.submitter_id <> p_user_id
    GROUP BY us.show_id
),
agg AS (
    SELECT sc.submitter_id,
        SUM(sc.actual) AS a_raw,
        SUM(sc.others_t::numeric / NULLIF(p.others_all, 0) * p.pool_v) AS e_raw,
        SUM(f.n * sc.actual::numeric / NULLIF(p.pool_v, 0)) AS a_w,
        SUM(f.n * sc.others_t::numeric / NULLIF(p.others_all, 0)) AS e_w,
        SUM(f.n * f.n * sc.others_t::numeric / (NULLIF(p.others_all, 0) * NULLIF(p.pool_v, 0))) AS v_w
    FROM sc
    JOIN pool p ON p.show_id = sc.show_id
    JOIN vfield f ON f.show_id = sc.show_id
    WHERE p.pool_v > 0
    GROUP BY sc.submitter_id
),
parts AS (
    SELECT sg.submitter_id,
        COUNT(*) AS parts,
        COUNT(uv.id) AS votings_nonblank,
        COUNT(*) FILTER (WHERE uv.score = pm.max_score) AS votings_max
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song sg ON sg.id = ss.song_id
    JOIN show sh ON sh.id = us.show_id
    JOIN point_max pm ON pm.point_system_id = sh.point_system_id
    LEFT JOIN vote_set uvs ON uvs.show_id = us.show_id AND uvs.voter_id = p_user_id
    LEFT JOIN vote uv ON uv.vote_set_id = uvs.id AND uv.song_id = sg.id
    WHERE sg.submitter_id <> p_user_id
    GROUP BY sg.submitter_id
),
reciprocal AS (
    SELECT vs.voter_id AS submitter_id,
        SUM(v.score) AS pts_target_to_user,
        COUNT(*) AS received_any,
        COUNT(*) FILTER (WHERE v.score = pm.max_score) AS received_max
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    JOIN point_max pm ON pm.point_system_id = sh.point_system_id
    WHERE s.submitter_id = p_user_id
      AND vs.voter_id <> p_user_id
      AND sh.status = 'full'
      AND (
          (sh.year_id > 0
           AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
           AND (p_year_to IS NULL OR sh.year_id <= p_year_to))
          OR (p_include_specials AND sh.year_id < 0)
      )
    GROUP BY vs.voter_id
)
SELECT pt.submitter_id,
    acc.username AS submitter_name,
    pt.parts, pt.votings_nonblank, pt.votings_max,
    COALESCE(ag.a_raw, 0)::bigint AS given,
    round(COALESCE(ag.e_raw, 0), 2) AS expected,
    round(bias_ratio(COALESCE(ag.a_raw, 0), COALESCE(ag.e_raw, 0)), 4) AS bias,
    round(bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)), 4) AS log_ratio,
    round(bias_zw(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.v_w, 0)), 3) AS z,
    bias_class(pt.parts, COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.e_raw, 0)) AS bias_class,
    COALESCE(rp.pts_target_to_user, 0)::bigint AS received,
    (COALESCE(ag.a_raw, 0) - COALESCE(rp.pts_target_to_user, 0))::bigint AS deficit,
    COALESCE(rp.received_any, 0)::bigint AS received_any,
    COALESCE(rp.received_max, 0)::bigint AS received_max,
    CASE WHEN rp.pts_target_to_user > 0
        THEN round(COALESCE(ag.a_raw, 0)::numeric / rp.pts_target_to_user - 1, 4)
        ELSE 0 END AS reciprocal_bias,
    bias_reciprocal_class(pt.parts, rp.pts_target_to_user,
        CASE WHEN rp.pts_target_to_user > 0
            THEN COALESCE(ag.a_raw, 0)::numeric / rp.pts_target_to_user - 1
            ELSE 0 END) AS reciprocal_bias_class
FROM parts pt
LEFT JOIN agg ag ON ag.submitter_id = pt.submitter_id
LEFT JOIN reciprocal rp ON rp.submitter_id = pt.submitter_id
LEFT JOIN account acc ON acc.id = pt.submitter_id
ORDER BY bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)) DESC, pt.parts DESC
$$;

CREATE OR REPLACE FUNCTION country_voter_bias(
    p_country_id text,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL
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
    SELECT point_system_id, MAX(score) AS max_score FROM point GROUP BY point_system_id
),
voter_shows AS (
    SELECT vs.voter_id AS v, vs.show_id
    FROM vote_set vs
    JOIN show sh ON sh.id = vs.show_id
    WHERE sh.status = 'full'
      AND sh.year_id > 0
      AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
      AND (p_year_to IS NULL OR sh.year_id <= p_year_to)
),
sc AS (
    SELECT vss.v, vss.show_id, s.country_id,
        COALESCE(SUM(vo.score) FILTER (WHERE iv.voter_id = vss.v), 0) AS actual,
        COALESCE(SUM(vo.score) FILTER (WHERE iv.voter_id <> vss.v), 0) AS others_t
    FROM voter_shows vss
    JOIN vote_set iv ON iv.show_id = vss.show_id
    JOIN vote vo ON vo.vote_set_id = iv.id
    JOIN song s ON s.id = vo.song_id
    WHERE s.submitter_id <> vss.v
    GROUP BY vss.v, vss.show_id, s.country_id
),
pool AS (
    SELECT v, show_id, SUM(actual) AS pool_v, SUM(others_t) AS others_all
    FROM sc GROUP BY v, show_id
),
vfield AS (
    SELECT vss.v, vss.show_id, COUNT(DISTINCT s.id) AS n
    FROM voter_shows vss
    JOIN song_show ss ON ss.show_id = vss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> vss.v
    GROUP BY vss.v, vss.show_id
),
agg AS (
    SELECT sc.v,
        SUM(sc.actual) FILTER (WHERE sc.country_id = p_country_id) AS a_raw,
        SUM((sc.others_t::numeric / NULLIF(p.others_all, 0)) * p.pool_v)
            FILTER (WHERE sc.country_id = p_country_id) AS e_raw,
        SUM(f.n * sc.actual::numeric / NULLIF(p.pool_v, 0))
            FILTER (WHERE sc.country_id = p_country_id) AS a_w,
        SUM(f.n * sc.others_t::numeric / NULLIF(p.others_all, 0))
            FILTER (WHERE sc.country_id = p_country_id) AS e_w,
        SUM(f.n * f.n * sc.others_t::numeric / (NULLIF(p.others_all, 0) * NULLIF(p.pool_v, 0)))
            FILTER (WHERE sc.country_id = p_country_id) AS v_w
    FROM sc
    JOIN pool p ON p.v = sc.v AND p.show_id = sc.show_id
    JOIN vfield f ON f.v = sc.v AND f.show_id = sc.show_id
    WHERE p.pool_v > 0
    GROUP BY sc.v
),
parts AS (
    SELECT vss.v,
        COUNT(*) AS parts,
        COUNT(uv.id) AS votings_nonblank,
        COUNT(*) FILTER (WHERE uv.score = pm.max_score) AS votings_max
    FROM voter_shows vss
    JOIN song_show ss ON ss.show_id = vss.show_id
    JOIN song s ON s.id = ss.song_id
    JOIN show sh ON sh.id = vss.show_id
    JOIN point_max pm ON pm.point_system_id = sh.point_system_id
    LEFT JOIN vote_set uvs ON uvs.show_id = vss.show_id AND uvs.voter_id = vss.v
    LEFT JOIN vote uv ON uv.vote_set_id = uvs.id AND uv.song_id = s.id
    WHERE s.country_id = p_country_id AND s.submitter_id <> vss.v
    GROUP BY vss.v
)
SELECT pt.v AS voter_id,
    acc.username AS voter_name,
    pt.parts, pt.votings_nonblank, pt.votings_max,
    COALESCE(ag.a_raw, 0)::bigint AS given,
    round(COALESCE(ag.e_raw, 0), 2) AS expected,
    round(bias_ratio(COALESCE(ag.a_raw, 0), COALESCE(ag.e_raw, 0)), 4) AS bias,
    round(bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)), 4) AS log_ratio,
    round(bias_zw(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.v_w, 0)), 3) AS z,
    bias_class(pt.parts, COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.e_raw, 0)) AS bias_class
FROM parts pt
LEFT JOIN agg ag ON ag.v = pt.v
LEFT JOIN account acc ON acc.id = pt.v
ORDER BY bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)) DESC, pt.parts DESC
$$;

CREATE OR REPLACE FUNCTION submitter_voter_bias(
    p_submitter_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_specials boolean DEFAULT true
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
    bias_class text,
    received bigint,
    deficit bigint,
    received_any bigint,
    received_max bigint,
    reciprocal_bias numeric,
    reciprocal_bias_class text
)
LANGUAGE sql STABLE AS $$
WITH point_max AS (
    SELECT point_system_id, MAX(score) AS max_score FROM point GROUP BY point_system_id
),
voter_shows AS (
    SELECT vs.voter_id AS v, vs.show_id
    FROM vote_set vs
    JOIN show sh ON sh.id = vs.show_id
    WHERE sh.status = 'full'
      AND (
          (sh.year_id > 0
           AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
           AND (p_year_to IS NULL OR sh.year_id <= p_year_to))
          OR (p_include_specials AND sh.year_id < 0)
      )
),
sc AS (
    SELECT vss.v, vss.show_id, s.submitter_id,
        COALESCE(SUM(vo.score) FILTER (WHERE iv.voter_id = vss.v), 0) AS actual,
        COALESCE(SUM(vo.score) FILTER (WHERE iv.voter_id <> vss.v), 0) AS others_t
    FROM voter_shows vss
    JOIN vote_set iv ON iv.show_id = vss.show_id
    JOIN vote vo ON vo.vote_set_id = iv.id
    JOIN song s ON s.id = vo.song_id
    WHERE s.submitter_id <> vss.v
    GROUP BY vss.v, vss.show_id, s.submitter_id
),
pool AS (
    SELECT v, show_id, SUM(actual) AS pool_v, SUM(others_t) AS others_all
    FROM sc GROUP BY v, show_id
),
vfield AS (
    SELECT vss.v, vss.show_id, COUNT(DISTINCT s.id) AS n
    FROM voter_shows vss
    JOIN song_show ss ON ss.show_id = vss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> vss.v
    GROUP BY vss.v, vss.show_id
),
agg AS (
    SELECT sc.v,
        SUM(sc.actual) FILTER (WHERE sc.submitter_id = p_submitter_id) AS a_raw,
        SUM((sc.others_t::numeric / NULLIF(p.others_all, 0)) * p.pool_v)
            FILTER (WHERE sc.submitter_id = p_submitter_id) AS e_raw,
        SUM(f.n * sc.actual::numeric / NULLIF(p.pool_v, 0))
            FILTER (WHERE sc.submitter_id = p_submitter_id) AS a_w,
        SUM(f.n * sc.others_t::numeric / NULLIF(p.others_all, 0))
            FILTER (WHERE sc.submitter_id = p_submitter_id) AS e_w,
        SUM(f.n * f.n * sc.others_t::numeric / (NULLIF(p.others_all, 0) * NULLIF(p.pool_v, 0)))
            FILTER (WHERE sc.submitter_id = p_submitter_id) AS v_w
    FROM sc
    JOIN pool p ON p.v = sc.v AND p.show_id = sc.show_id
    JOIN vfield f ON f.v = sc.v AND f.show_id = sc.show_id
    WHERE p.pool_v > 0
    GROUP BY sc.v
),
parts AS (
    SELECT vss.v,
        COUNT(*) AS parts,
        COUNT(uv.id) AS votings_nonblank,
        COUNT(*) FILTER (WHERE uv.score = pm.max_score) AS votings_max
    FROM voter_shows vss
    JOIN song_show ss ON ss.show_id = vss.show_id
    JOIN song s ON s.id = ss.song_id
    JOIN show sh ON sh.id = vss.show_id
    JOIN point_max pm ON pm.point_system_id = sh.point_system_id
    LEFT JOIN vote_set uvs ON uvs.show_id = vss.show_id AND uvs.voter_id = vss.v
    LEFT JOIN vote uv ON uv.vote_set_id = uvs.id AND uv.song_id = s.id
    WHERE s.submitter_id = p_submitter_id AND s.submitter_id <> vss.v
    GROUP BY vss.v
),
reciprocal AS (
    SELECT s.submitter_id AS v,
        SUM(vo.score) AS pts_target_to_voter,
        COUNT(*) AS received_any,
        COUNT(*) FILTER (WHERE vo.score = pm.max_score) AS received_max
    FROM vote_set vs
    JOIN vote vo ON vo.vote_set_id = vs.id
    JOIN song s ON s.id = vo.song_id
    JOIN show sh ON sh.id = vs.show_id
    JOIN point_max pm ON pm.point_system_id = sh.point_system_id
    WHERE vs.voter_id = p_submitter_id
      AND s.submitter_id <> p_submitter_id
      AND sh.status = 'full'
      AND (
          (sh.year_id > 0
           AND (p_year_from IS NULL OR sh.year_id >= p_year_from)
           AND (p_year_to IS NULL OR sh.year_id <= p_year_to))
          OR (p_include_specials AND sh.year_id < 0)
      )
    GROUP BY s.submitter_id
)
SELECT pt.v AS voter_id,
    acc.username AS voter_name,
    pt.parts, pt.votings_nonblank, pt.votings_max,
    COALESCE(ag.a_raw, 0)::bigint AS given,
    round(COALESCE(ag.e_raw, 0), 2) AS expected,
    round(bias_ratio(COALESCE(ag.a_raw, 0), COALESCE(ag.e_raw, 0)), 4) AS bias,
    round(bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)), 4) AS log_ratio,
    round(bias_zw(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.v_w, 0)), 3) AS z,
    bias_class(pt.parts, COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0), COALESCE(ag.e_raw, 0)) AS bias_class,
    COALESCE(rp.pts_target_to_voter, 0)::bigint AS received,
    (COALESCE(ag.a_raw, 0) - COALESCE(rp.pts_target_to_voter, 0))::bigint AS deficit,
    COALESCE(rp.received_any, 0)::bigint AS received_any,
    COALESCE(rp.received_max, 0)::bigint AS received_max,
    CASE WHEN rp.pts_target_to_voter > 0
        THEN round(COALESCE(ag.a_raw, 0)::numeric / rp.pts_target_to_voter - 1, 4)
        ELSE 0 END AS reciprocal_bias,
    bias_reciprocal_class(pt.parts, rp.pts_target_to_voter,
        CASE WHEN rp.pts_target_to_voter > 0
            THEN COALESCE(ag.a_raw, 0)::numeric / rp.pts_target_to_voter - 1
            ELSE 0 END) AS reciprocal_bias_class
FROM parts pt
LEFT JOIN agg ag ON ag.v = pt.v
LEFT JOIN reciprocal rp ON rp.v = pt.v
LEFT JOIN account acc ON acc.id = pt.v
ORDER BY bias_logratio(COALESCE(ag.a_w, 0), COALESCE(ag.e_w, 0)) DESC, pt.parts DESC
$$;
