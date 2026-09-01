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
    SELECT point_system_id, MAX(score) AS max_score
    FROM point
    GROUP BY point_system_id
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
    SELECT point_system_id, MAX(score) AS max_score
    FROM point
    GROUP BY point_system_id
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
