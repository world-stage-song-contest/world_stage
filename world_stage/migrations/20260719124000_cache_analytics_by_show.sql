BEGIN;

-- Cache additive, per-show analytics components.  Public analytics functions
-- aggregate these rows for their requested ballot mode, specials setting, and
-- year range instead of repeatedly joining every raw ballot.
CREATE TABLE taste_similarity_show_cache (
    show_id bigint NOT NULL REFERENCES show (id) ON DELETE CASCADE,
    year_id bigint NOT NULL REFERENCES year (id) ON UPDATE CASCADE ON DELETE CASCADE,
    ballot_mode text NOT NULL CHECK (ballot_mode IN ('official', 'effective')),
    voter_a_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    voter_b_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    co_voted_songs bigint NOT NULL,
    covariance numeric NOT NULL,
    variance_a numeric NOT NULL,
    variance_b numeric NOT NULL,
    PRIMARY KEY (show_id, ballot_mode, voter_a_id, voter_b_id),
    CHECK (voter_a_id < voter_b_id)
);

CREATE INDEX taste_similarity_cache_voter_a_idx
    ON taste_similarity_show_cache (voter_a_id, ballot_mode, year_id);
CREATE INDEX taste_similarity_cache_voter_b_idx
    ON taste_similarity_show_cache (voter_b_id, ballot_mode, year_id);

CREATE TABLE country_bias_show_cache (
    show_id bigint NOT NULL REFERENCES show (id) ON DELETE CASCADE,
    year_id bigint NOT NULL REFERENCES year (id) ON UPDATE CASCADE ON DELETE CASCADE,
    ballot_mode text NOT NULL CHECK (ballot_mode IN ('official', 'effective')),
    voter_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    country_id text NOT NULL REFERENCES country (id) ON UPDATE CASCADE ON DELETE CASCADE,
    parts bigint NOT NULL,
    votings_nonblank bigint NOT NULL,
    votings_max bigint NOT NULL,
    a_raw bigint NOT NULL,
    e_raw numeric NOT NULL,
    a_w numeric NOT NULL,
    e_w numeric NOT NULL,
    v_w numeric NOT NULL,
    PRIMARY KEY (show_id, ballot_mode, voter_id, country_id)
);

CREATE INDEX country_bias_cache_voter_idx
    ON country_bias_show_cache (voter_id, ballot_mode, year_id);
CREATE INDEX country_bias_cache_country_idx
    ON country_bias_show_cache (country_id, ballot_mode, year_id);

CREATE TABLE submitter_bias_show_cache (
    show_id bigint NOT NULL REFERENCES show (id) ON DELETE CASCADE,
    year_id bigint NOT NULL REFERENCES year (id) ON UPDATE CASCADE ON DELETE CASCADE,
    ballot_mode text NOT NULL CHECK (ballot_mode IN ('official', 'effective')),
    voter_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    submitter_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    parts bigint NOT NULL,
    votings_nonblank bigint NOT NULL,
    votings_max bigint NOT NULL,
    a_raw bigint NOT NULL,
    e_raw numeric NOT NULL,
    a_w numeric NOT NULL,
    e_w numeric NOT NULL,
    v_w numeric NOT NULL,
    received bigint NOT NULL,
    received_any bigint NOT NULL,
    received_max bigint NOT NULL,
    PRIMARY KEY (show_id, ballot_mode, voter_id, submitter_id),
    CHECK (voter_id <> submitter_id)
);

CREATE INDEX submitter_bias_cache_voter_idx
    ON submitter_bias_show_cache (voter_id, ballot_mode, year_id);
CREATE INDEX submitter_bias_cache_submitter_idx
    ON submitter_bias_show_cache (submitter_id, ballot_mode, year_id);

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
        JOIN song s ON s.id = va.song_id
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
        SELECT MAX(p.score) AS max_score
        FROM show sh
        JOIN point p ON p.point_system_id = sh.point_system_id
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
        JOIN song s ON s.id = v.song_id
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
        JOIN song s ON s.id = ss.song_id
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
        JOIN song s ON s.id = ss.song_id
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
        SELECT MAX(p.score) AS max_score
        FROM show sh
        JOIN point p ON p.point_system_id = sh.point_system_id
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
        JOIN song s ON s.id = v.song_id
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
        JOIN song s ON s.id = ss.song_id
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
        JOIN song s ON s.id = ss.song_id
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
        JOIN song s ON s.id = v.song_id
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

-- The public functions keep their existing signatures and output shapes.  A
-- setting now selects cached rows rather than selecting a raw-ballot query.
CREATE OR REPLACE FUNCTION user_country_bias(
    p_user_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_revotes boolean DEFAULT false
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
WITH agg AS (
    SELECT cache.country_id,
        SUM(cache.parts)::bigint AS parts,
        SUM(cache.votings_nonblank)::bigint AS votings_nonblank,
        SUM(cache.votings_max)::bigint AS votings_max,
        SUM(cache.a_raw) AS a_raw,
        SUM(cache.e_raw) AS e_raw,
        SUM(cache.a_w) AS a_w,
        SUM(cache.e_w) AS e_w,
        SUM(cache.v_w) AS v_w
    FROM country_bias_show_cache cache
    WHERE cache.voter_id = p_user_id
      AND cache.ballot_mode = CASE WHEN p_include_revotes THEN 'effective' ELSE 'official' END
      AND cache.year_id > 0
      AND (p_year_from IS NULL OR cache.year_id >= p_year_from)
      AND (p_year_to IS NULL OR cache.year_id <= p_year_to)
    GROUP BY cache.country_id
)
SELECT agg.country_id,
    country.name,
    agg.parts,
    agg.votings_nonblank,
    agg.votings_max,
    agg.a_raw::bigint,
    round(agg.e_raw, 2),
    round(bias_ratio(agg.a_raw, agg.e_raw), 4),
    round(bias_logratio(agg.a_w, agg.e_w), 4),
    round(bias_zw(agg.a_w, agg.e_w, agg.v_w), 3),
    bias_class(agg.parts, agg.a_w, agg.e_w, agg.e_raw)
FROM agg
LEFT JOIN country ON country.id = agg.country_id
ORDER BY bias_logratio(agg.a_w, agg.e_w) DESC, agg.parts DESC
$$;

CREATE OR REPLACE FUNCTION country_voter_bias(
    p_country_id text,
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
WITH agg AS (
    SELECT cache.voter_id,
        SUM(cache.parts)::bigint AS parts,
        SUM(cache.votings_nonblank)::bigint AS votings_nonblank,
        SUM(cache.votings_max)::bigint AS votings_max,
        SUM(cache.a_raw) AS a_raw,
        SUM(cache.e_raw) AS e_raw,
        SUM(cache.a_w) AS a_w,
        SUM(cache.e_w) AS e_w,
        SUM(cache.v_w) AS v_w
    FROM country_bias_show_cache cache
    WHERE cache.country_id = p_country_id
      AND cache.ballot_mode = CASE WHEN p_include_revotes THEN 'effective' ELSE 'official' END
      AND cache.year_id > 0
      AND (p_year_from IS NULL OR cache.year_id >= p_year_from)
      AND (p_year_to IS NULL OR cache.year_id <= p_year_to)
    GROUP BY cache.voter_id
)
SELECT agg.voter_id,
    account.username,
    agg.parts,
    agg.votings_nonblank,
    agg.votings_max,
    agg.a_raw::bigint,
    round(agg.e_raw, 2),
    round(bias_ratio(agg.a_raw, agg.e_raw), 4),
    round(bias_logratio(agg.a_w, agg.e_w), 4),
    round(bias_zw(agg.a_w, agg.e_w, agg.v_w), 3),
    bias_class(agg.parts, agg.a_w, agg.e_w, agg.e_raw)
FROM agg
LEFT JOIN account ON account.id = agg.voter_id
ORDER BY bias_logratio(agg.a_w, agg.e_w) DESC, agg.parts DESC
$$;

CREATE OR REPLACE FUNCTION user_submitter_bias(
    p_user_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_specials boolean DEFAULT true,
    p_include_revotes boolean DEFAULT false
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
WITH agg AS (
    SELECT cache.submitter_id,
        SUM(cache.parts)::bigint AS parts,
        SUM(cache.votings_nonblank)::bigint AS votings_nonblank,
        SUM(cache.votings_max)::bigint AS votings_max,
        SUM(cache.a_raw) AS a_raw,
        SUM(cache.e_raw) AS e_raw,
        SUM(cache.a_w) AS a_w,
        SUM(cache.e_w) AS e_w,
        SUM(cache.v_w) AS v_w,
        SUM(cache.received) AS received,
        SUM(cache.received_any)::bigint AS received_any,
        SUM(cache.received_max)::bigint AS received_max
    FROM submitter_bias_show_cache cache
    WHERE cache.voter_id = p_user_id
      AND cache.ballot_mode = CASE WHEN p_include_revotes THEN 'effective' ELSE 'official' END
      AND (
          (cache.year_id > 0
           AND (p_year_from IS NULL OR cache.year_id >= p_year_from)
           AND (p_year_to IS NULL OR cache.year_id <= p_year_to))
          OR (p_include_specials AND cache.year_id < 0)
      )
    GROUP BY cache.submitter_id
    HAVING SUM(cache.parts) > 0
)
SELECT agg.submitter_id,
    account.username,
    agg.parts,
    agg.votings_nonblank,
    agg.votings_max,
    agg.a_raw::bigint,
    round(agg.e_raw, 2),
    round(bias_ratio(agg.a_raw, agg.e_raw), 4),
    round(bias_logratio(agg.a_w, agg.e_w), 4),
    round(bias_zw(agg.a_w, agg.e_w, agg.v_w), 3),
    bias_class(agg.parts, agg.a_w, agg.e_w, agg.e_raw),
    agg.received::bigint,
    (agg.a_raw - agg.received)::bigint,
    agg.received_any,
    agg.received_max,
    CASE WHEN agg.received > 0
        THEN round(agg.a_raw / agg.received - 1, 4)
        ELSE 0 END,
    bias_reciprocal_class(agg.parts, agg.received,
        CASE WHEN agg.received > 0 THEN agg.a_raw / agg.received - 1 ELSE 0 END)
FROM agg
LEFT JOIN account ON account.id = agg.submitter_id
ORDER BY bias_logratio(agg.a_w, agg.e_w) DESC, agg.parts DESC
$$;

CREATE OR REPLACE FUNCTION submitter_voter_bias(
    p_submitter_id bigint,
    p_year_from bigint DEFAULT NULL,
    p_year_to bigint DEFAULT NULL,
    p_include_specials boolean DEFAULT true,
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
    bias_class text,
    received bigint,
    deficit bigint,
    received_any bigint,
    received_max bigint,
    reciprocal_bias numeric,
    reciprocal_bias_class text
)
LANGUAGE sql STABLE AS $$
WITH agg AS (
    SELECT cache.voter_id,
        SUM(cache.parts)::bigint AS parts,
        SUM(cache.votings_nonblank)::bigint AS votings_nonblank,
        SUM(cache.votings_max)::bigint AS votings_max,
        SUM(cache.a_raw) AS a_raw,
        SUM(cache.e_raw) AS e_raw,
        SUM(cache.a_w) AS a_w,
        SUM(cache.e_w) AS e_w,
        SUM(cache.v_w) AS v_w,
        SUM(cache.received) AS received,
        SUM(cache.received_any)::bigint AS received_any,
        SUM(cache.received_max)::bigint AS received_max
    FROM submitter_bias_show_cache cache
    WHERE cache.submitter_id = p_submitter_id
      AND cache.ballot_mode = CASE WHEN p_include_revotes THEN 'effective' ELSE 'official' END
      AND (
          (cache.year_id > 0
           AND (p_year_from IS NULL OR cache.year_id >= p_year_from)
           AND (p_year_to IS NULL OR cache.year_id <= p_year_to))
          OR (p_include_specials AND cache.year_id < 0)
      )
    GROUP BY cache.voter_id
    HAVING SUM(cache.parts) > 0
)
SELECT agg.voter_id,
    account.username,
    agg.parts,
    agg.votings_nonblank,
    agg.votings_max,
    agg.a_raw::bigint,
    round(agg.e_raw, 2),
    round(bias_ratio(agg.a_raw, agg.e_raw), 4),
    round(bias_logratio(agg.a_w, agg.e_w), 4),
    round(bias_zw(agg.a_w, agg.e_w, agg.v_w), 3),
    bias_class(agg.parts, agg.a_w, agg.e_w, agg.e_raw),
    agg.received::bigint,
    (agg.a_raw - agg.received)::bigint,
    agg.received_any,
    agg.received_max,
    CASE WHEN agg.received > 0
        THEN round(agg.a_raw / agg.received - 1, 4)
        ELSE 0 END,
    bias_reciprocal_class(agg.parts, agg.received,
        CASE WHEN agg.received > 0 THEN agg.a_raw / agg.received - 1 ELSE 0 END)
FROM agg
LEFT JOIN account ON account.id = agg.voter_id
ORDER BY bias_logratio(agg.a_w, agg.e_w) DESC, agg.parts DESC
$$;

CREATE OR REPLACE FUNCTION user_taste_similarity(
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
WITH agg AS (
    SELECT CASE
            WHEN cache.voter_a_id = p_user_id THEN cache.voter_b_id
            ELSE cache.voter_a_id
        END AS other_id,
        COUNT(*)::bigint AS shared_shows,
        SUM(cache.co_voted_songs)::bigint AS co_voted_songs,
        SUM(cache.covariance) AS covariance,
        SUM(CASE
            WHEN cache.voter_a_id = p_user_id THEN cache.variance_a
            ELSE cache.variance_b
        END) AS target_variance,
        SUM(CASE
            WHEN cache.voter_a_id = p_user_id THEN cache.variance_b
            ELSE cache.variance_a
        END) AS other_variance
    FROM taste_similarity_show_cache cache
    WHERE (cache.voter_a_id = p_user_id OR cache.voter_b_id = p_user_id)
      AND cache.ballot_mode = CASE WHEN p_include_revotes THEN 'effective' ELSE 'official' END
      AND (
          (cache.year_id > 0
           AND (p_year_from IS NULL OR cache.year_id >= p_year_from)
           AND (p_year_to IS NULL OR cache.year_id <= p_year_to))
          OR (p_include_specials AND cache.year_id < 0)
      )
    GROUP BY 1
),
scored AS (
    SELECT agg.*,
        CASE WHEN target_variance > 0 AND other_variance > 0
            THEN covariance / sqrt(target_variance * other_variance)
            ELSE NULL END AS similarity
    FROM agg
)
SELECT scored.other_id,
    account.username,
    scored.shared_shows,
    scored.co_voted_songs,
    scored.similarity,
    CASE
        WHEN scored.shared_shows < 5 OR scored.similarity IS NULL THEN 'inconclusive'
        WHEN scored.similarity >= 0.20 THEN 'very-positive'
        WHEN scored.similarity >= 0.075 THEN 'positive'
        WHEN scored.similarity > -0.05 THEN 'neutral'
        WHEN scored.similarity > -0.20 THEN 'negative'
        ELSE 'very-negative'
    END
FROM scored
JOIN account ON account.id = scored.other_id
ORDER BY scored.similarity DESC NULLS LAST, scored.shared_shows DESC
$$;

-- Deduplicate invalidations within a transaction, including multi-row ballot
-- writes. Official changes invalidate both modes because effective mode falls
-- back to official ballots for voters who have not Revoted.
CREATE TABLE analytics_show_refresh_queue (
    show_id bigint NOT NULL,
    ballot_mode text NOT NULL CHECK (ballot_mode IN ('official', 'effective')),
    PRIMARY KEY (show_id, ballot_mode)
);

CREATE OR REPLACE FUNCTION queue_analytics_show_refresh(
    p_show_id bigint,
    p_result_mode text
)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF p_show_id IS NULL OR p_result_mode IS NULL THEN
        RETURN;
    END IF;
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM show WHERE id = p_show_id AND status = 'full') THEN
        RETURN;
    END IF;

    IF p_result_mode = 'official' THEN
        INSERT INTO analytics_show_refresh_queue (show_id, ballot_mode)
        VALUES (p_show_id, 'official')
        ON CONFLICT DO NOTHING;
    END IF;

    INSERT INTO analytics_show_refresh_queue (show_id, ballot_mode)
    VALUES (p_show_id, 'effective')
    ON CONFLICT DO NOTHING;
END;
$$;

CREATE OR REPLACE FUNCTION trigger_queue_analytics_from_vote()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_show_id bigint;
    v_result_mode text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        SELECT show_id, result_mode INTO v_show_id, v_result_mode
        FROM vote_set WHERE id = OLD.vote_set_id;
        IF FOUND THEN
            PERFORM queue_analytics_show_refresh(v_show_id, v_result_mode);
        END IF;
    END IF;

    IF TG_OP <> 'DELETE'
       AND (TG_OP <> 'UPDATE' OR NEW.vote_set_id IS DISTINCT FROM OLD.vote_set_id) THEN
        SELECT show_id, result_mode INTO v_show_id, v_result_mode
        FROM vote_set WHERE id = NEW.vote_set_id;
        IF FOUND THEN
            PERFORM queue_analytics_show_refresh(v_show_id, v_result_mode);
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE OR REPLACE FUNCTION trigger_queue_analytics_from_vote_set()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM queue_analytics_show_refresh(OLD.show_id, OLD.result_mode);
    END IF;
    IF TG_OP <> 'DELETE' AND (
        TG_OP <> 'UPDATE'
        OR NEW.show_id IS DISTINCT FROM OLD.show_id
        OR NEW.result_mode IS DISTINCT FROM OLD.result_mode
        OR NEW.voter_id IS DISTINCT FROM OLD.voter_id
    ) THEN
        PERFORM queue_analytics_show_refresh(NEW.show_id, NEW.result_mode);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE OR REPLACE FUNCTION trigger_queue_analytics_from_show()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM 'full' THEN
        DELETE FROM taste_similarity_show_cache WHERE show_id = NEW.id;
        DELETE FROM country_bias_show_cache WHERE show_id = NEW.id;
        DELETE FROM submitter_bias_show_cache WHERE show_id = NEW.id;
    ELSIF TG_OP = 'INSERT'
       OR OLD.status IS DISTINCT FROM NEW.status
       OR OLD.year_id IS DISTINCT FROM NEW.year_id
       OR OLD.point_system_id IS DISTINCT FROM NEW.point_system_id THEN
        PERFORM queue_analytics_show_refresh(NEW.id, 'official');
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trigger_queue_analytics_from_song_show()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM queue_analytics_show_refresh(OLD.show_id, 'official');
    END IF;
    IF TG_OP <> 'DELETE'
       AND (TG_OP <> 'UPDATE' OR NEW.show_id IS DISTINCT FROM OLD.show_id) THEN
        PERFORM queue_analytics_show_refresh(NEW.show_id, 'official');
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE OR REPLACE FUNCTION trigger_queue_analytics_from_song()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_show_id bigint;
BEGIN
    FOR v_show_id IN
        SELECT show_id FROM song_show WHERE song_id = NEW.id
    LOOP
        PERFORM queue_analytics_show_refresh(v_show_id, 'official');
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trigger_queue_analytics_from_point()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_show_id bigint;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        FOR v_show_id IN SELECT id FROM show WHERE point_system_id = OLD.point_system_id LOOP
            PERFORM queue_analytics_show_refresh(v_show_id, 'official');
        END LOOP;
    END IF;
    IF TG_OP <> 'DELETE'
       AND (TG_OP <> 'UPDATE' OR NEW.point_system_id IS DISTINCT FROM OLD.point_system_id) THEN
        FOR v_show_id IN SELECT id FROM show WHERE point_system_id = NEW.point_system_id LOOP
            PERFORM queue_analytics_show_refresh(v_show_id, 'official');
        END LOOP;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE OR REPLACE FUNCTION process_analytics_show_refresh_queue()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM refresh_analytics_show_cache(NEW.show_id, NEW.ballot_mode);
    DELETE FROM analytics_show_refresh_queue
    WHERE show_id = NEW.show_id AND ballot_mode = NEW.ballot_mode;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_queue_analytics_on_vote
    AFTER INSERT OR UPDATE OR DELETE ON vote
    FOR EACH ROW EXECUTE FUNCTION trigger_queue_analytics_from_vote();

CREATE TRIGGER trg_queue_analytics_on_vote_set
    AFTER INSERT OR UPDATE OR DELETE ON vote_set
    FOR EACH ROW EXECUTE FUNCTION trigger_queue_analytics_from_vote_set();

CREATE TRIGGER trg_queue_analytics_on_show
    AFTER INSERT OR UPDATE OF status, year_id, point_system_id ON show
    FOR EACH ROW EXECUTE FUNCTION trigger_queue_analytics_from_show();

CREATE TRIGGER trg_queue_analytics_on_song_show
    AFTER INSERT OR UPDATE OF song_id, show_id OR DELETE ON song_show
    FOR EACH ROW EXECUTE FUNCTION trigger_queue_analytics_from_song_show();

CREATE TRIGGER trg_queue_analytics_on_song
    AFTER UPDATE OF country_id, submitter_id ON song
    FOR EACH ROW
    WHEN (OLD.country_id IS DISTINCT FROM NEW.country_id
       OR OLD.submitter_id IS DISTINCT FROM NEW.submitter_id)
    EXECUTE FUNCTION trigger_queue_analytics_from_song();

CREATE TRIGGER trg_queue_analytics_on_point
    AFTER INSERT OR UPDATE OF point_system_id, score OR DELETE ON point
    FOR EACH ROW EXECUTE FUNCTION trigger_queue_analytics_from_point();

CREATE CONSTRAINT TRIGGER trg_process_analytics_show_refresh_queue
    AFTER INSERT ON analytics_show_refresh_queue
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION process_analytics_show_refresh_queue();

-- Existing published shows need an initial cache before the public functions
-- are switched over by this migration.
DO $$
DECLARE
    show_row record;
BEGIN
    FOR show_row IN SELECT id FROM show WHERE status = 'full' LOOP
        PERFORM refresh_analytics_show_cache(show_row.id, 'official');
        PERFORM refresh_analytics_show_cache(show_row.id, 'effective');
    END LOOP;
END;
$$;

ANALYZE taste_similarity_show_cache;
ANALYZE country_bias_show_cache;
ANALYZE submitter_bias_show_cache;

COMMIT;
