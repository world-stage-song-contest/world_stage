-- National-final ballots describe a selection process rather than the main
-- contest. Keep them out of every forward and inverse bias report.

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
    JOIN show sh ON sh.id = cache.show_id
    WHERE cache.voter_id = p_user_id
      AND sh.national_final_id IS NULL
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
    JOIN show sh ON sh.id = cache.show_id
    WHERE cache.country_id = p_country_id
      AND sh.national_final_id IS NULL
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
    JOIN show sh ON sh.id = cache.show_id
    WHERE cache.voter_id = p_user_id
      AND sh.national_final_id IS NULL
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
    JOIN show sh ON sh.id = cache.show_id
    WHERE cache.submitter_id = p_submitter_id
      AND sh.national_final_id IS NULL
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
