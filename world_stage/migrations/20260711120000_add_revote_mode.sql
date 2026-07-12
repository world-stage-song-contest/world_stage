-- Permanent re-voting for fully published shows.
-- Original ballots and results remain the canonical `official` mode.

ALTER TABLE show
    ADD COLUMN IF NOT EXISTS revote_eligible_at timestamptz;

ALTER TABLE vote_set
    ADD COLUMN IF NOT EXISTS result_mode text NOT NULL DEFAULT 'official',
    ADD CONSTRAINT vote_set_result_mode_check
        CHECK (result_mode IN ('official', 'revote'));

ALTER TABLE vote_set DROP CONSTRAINT IF EXISTS vote_set_voter_id_show_id_key;
DO $$
DECLARE
    old_constraint record;
    old_index record;
BEGIN
    -- Older imports used generated constraint names, so remove the former
    -- two-column uniqueness rule by its columns rather than by its name.
    FOR old_constraint IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'vote_set'::regclass
          AND c.contype = 'u'
          AND (
              SELECT array_agg(a.attname::text ORDER BY key_columns.ordinality)
              FROM unnest(c.conkey) WITH ORDINALITY AS key_columns(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = key_columns.attnum
          ) = ARRAY['voter_id', 'show_id']
    LOOP
        EXECUTE format('ALTER TABLE vote_set DROP CONSTRAINT %I', old_constraint.conname);
    END LOOP;

    -- Some legacy imports represented this rule as a standalone unique index.
    FOR old_index IN
        SELECT i.indexrelid::regclass AS index_name
        FROM pg_index i
        WHERE i.indrelid = 'vote_set'::regclass
          AND i.indisunique
          AND NOT i.indisprimary
          AND (
              SELECT array_agg(a.attname::text ORDER BY key_columns.ordinality)
              FROM unnest(i.indkey) WITH ORDINALITY AS key_columns(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = i.indrelid AND a.attnum = key_columns.attnum
          ) = ARRAY['voter_id', 'show_id']
    LOOP
        EXECUTE format('DROP INDEX %s', old_index.index_name);
    END LOOP;
END;
$$;
ALTER TABLE vote_set
    ADD CONSTRAINT vote_set_voter_show_mode_key UNIQUE (voter_id, show_id, result_mode);

ALTER TABLE country_show_results
    ADD COLUMN IF NOT EXISTS result_mode text NOT NULL DEFAULT 'official',
    ADD CONSTRAINT country_show_results_result_mode_check
        CHECK (result_mode IN ('official', 'revote'));

ALTER TABLE country_show_results DROP CONSTRAINT IF EXISTS pk_country_show_results;
ALTER TABLE country_show_results
    ADD CONSTRAINT pk_country_show_results PRIMARY KEY (song_id, show_id, result_mode);

CREATE INDEX IF NOT EXISTS idx_vote_set_show_mode ON vote_set (show_id, result_mode);
CREATE INDEX IF NOT EXISTS idx_csr_show_mode_place
    ON country_show_results (show_id, result_mode, place);

-- Existing fully published shows in closed years are immediately and permanently eligible.
UPDATE show
SET revote_eligible_at = CURRENT_TIMESTAMP
FROM year
WHERE show.year_id = year.id
  AND year.status = 'closed'
  AND show.status = 'full'
  AND show.revote_eligible_at IS NULL;

CREATE OR REPLACE FUNCTION mark_revote_eligibility_for_closed_year()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'closed' AND OLD.status IS DISTINCT FROM 'closed' THEN
        UPDATE show
        SET revote_eligible_at = COALESCE(revote_eligible_at, CURRENT_TIMESTAMP)
        WHERE year_id = NEW.id AND status = 'full';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION mark_revote_eligibility_for_full_show()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'full' AND NEW.revote_eligible_at IS NULL
       AND EXISTS (SELECT 1 FROM year WHERE id = NEW.year_id AND status = 'closed') THEN
        NEW.revote_eligible_at := CURRENT_TIMESTAMP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mark_revote_eligibility_for_closed_year ON year;
CREATE TRIGGER trg_mark_revote_eligibility_for_closed_year
    AFTER UPDATE OF status ON year
    FOR EACH ROW
    EXECUTE FUNCTION mark_revote_eligibility_for_closed_year();

DROP TRIGGER IF EXISTS trg_mark_revote_eligibility_for_full_show ON show;
CREATE TRIGGER trg_mark_revote_eligibility_for_full_show
    BEFORE INSERT OR UPDATE OF status ON show
    FOR EACH ROW
    EXECUTE FUNCTION mark_revote_eligibility_for_full_show();

-- The single source of truth for ballots used in each result mode.
CREATE OR REPLACE FUNCTION effective_show_vote_sets(p_show_id bigint, p_result_mode text)
RETURNS SETOF vote_set AS $$
    SELECT vs.*
    FROM vote_set vs
    WHERE vs.show_id = p_show_id
      AND (
          (p_result_mode = 'official' AND vs.result_mode = 'official')
          OR (
              p_result_mode = 'revote'
              AND vs.result_mode = 'revote'
          )
          OR (
              p_result_mode = 'revote'
              AND vs.result_mode = 'official'
              AND NOT EXISTS (
                  SELECT 1 FROM vote_set replacement
                  WHERE replacement.show_id = vs.show_id
                    AND replacement.voter_id = vs.voter_id
                    AND replacement.result_mode = 'revote'
              )
          )
      );
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION refresh_show_results_for_mode(
    p_show_id bigint, p_result_mode text
)
RETURNS void AS $$
DECLARE
    v_max_point integer;
    v_inserted integer;
BEGIN
    IF p_result_mode NOT IN ('official', 'revote') THEN
        RAISE EXCEPTION 'Unknown result mode: %', p_result_mode;
    END IF;

    SELECT MAX(p.score)
    INTO v_max_point
    FROM show sh
    JOIN point p ON p.point_system_id = sh.point_system_id
    WHERE sh.id = p_show_id;

    DELETE FROM country_show_results
    WHERE show_id = p_show_id AND result_mode = p_result_mode;

    WITH si AS (
        SELECT sh.id, sh.year_id, sh.dtf, sh.sc, sh.special, sh.short_name, sh.show_name
        FROM show sh WHERE sh.id = p_show_id
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
        WHERE
            (si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979)
            OR (si.year_id BETWEEN 1965 AND 1978 AND vs.country_id IS DISTINCT FROM s.country_id)
            OR (si.year_id >= 0 AND si.year_id < 1965 AND si.year_id <> 1960
                AND vs.country_id IS DISTINCT FROM s.country_id)
            OR (si.year_id = 1960 AND (
                vs.country_id IS DISTINCT FROM s.country_id
                OR (vs.country_id = s.country_id AND v.score = 1)
            ))
        GROUP BY s.id, s.country_id, v.score
    ),
    aggregated AS (
        SELECT
            s.country_id, c.name AS country_name, si.id AS show_id, s.id AS song_id,
            ss.running_order, si.show_name, si.short_name, si.year_id,
            si.dtf, si.sc, si.special,
            GREATEST(COALESCE(SUM(pc.score * pc.cnt), 0) - COALESCE(ss.penalty, 0), 0)
                AS total_points,
            COALESCE(SUM(pc.cnt), 0) AS total_votes_received,
            COALESCE(
                JSONB_OBJECT_AGG(pc.score::text, pc.cnt ORDER BY pc.score DESC)
                    FILTER (WHERE pc.score IS NOT NULL),
                '{}'::jsonb
            ) AS point_distribution,
            COALESCE(
                STRING_AGG(
                    LPAD(pc.score::text, 3, '0') || ':' || LPAD(pc.cnt::text, 3, '0'),
                    ',' ORDER BY pc.score DESC
                ) FILTER (WHERE pc.score IS NOT NULL),
                ''
            ) AS countback_string,
            (SELECT total_valid FROM totals) AS total_voters,
            CASE
                WHEN v_max_point IS NULL THEN 0
                WHEN si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979
                    THEN v_max_point * (SELECT total_valid FROM totals)
                WHEN (si.year_id BETWEEN 1966 AND 1978)
                     OR (si.year_id = 1965 AND si.short_name = 'f')
                    THEN v_max_point * GREATEST(
                        (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0), 0
                    )
                WHEN si.year_id = 1960
                    THEN v_max_point * GREATEST(
                        (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0), 0
                    ) + COALESCE(vbc.cnt, 0)
                ELSE v_max_point * GREATEST(
                    (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0), 0
                )
            END AS max_possible_points_calc
        FROM si
        JOIN song_show ss ON ss.show_id = si.id
        JOIN song s ON s.id = ss.song_id
        JOIN country c ON c.id = s.country_id
        LEFT JOIN point_counts pc ON pc.song_id = s.id
        LEFT JOIN voters_by_country vbc ON vbc.country_id = s.country_id
        WHERE c.is_participating
        GROUP BY s.country_id, c.name, si.id, s.id, ss.running_order, ss.penalty,
            si.show_name, si.short_name, si.year_id, si.dtf, si.sc, si.special, vbc.cnt
    ),
    ranked AS (
        SELECT a.*, DENSE_RANK() OVER (
            ORDER BY total_points DESC, total_votes_received DESC, countback_string DESC
        ) AS place, COUNT(*) OVER () AS total_countries
        FROM aggregated a
    )
    INSERT INTO country_show_results (
        country_id, country_name, show_id, show_name, short_name, year_id,
        song_id, running_order, total_points, total_votes_received, point_distribution,
        place, total_countries, placement_percentage, max_possible_points,
        points_percentage, entry_status, max_pts, total_voters, result_mode
    )
    SELECT
        r.country_id, r.country_name, r.show_id, r.show_name, r.short_name, r.year_id,
        r.song_id, r.running_order, r.total_points, r.total_votes_received,
        r.point_distribution, r.place, r.total_countries,
        ROUND(CASE
            WHEN r.place = r.total_countries THEN 0
            WHEN r.total_countries > 1 THEN ((r.total_countries - r.place)::numeric
                / (r.total_countries - 1)) * 100
            ELSE 100
        END, 2),
        COALESCE(r.max_possible_points_calc, 0),
        ROUND(COALESCE(r.total_points::numeric
            / NULLIF(r.max_possible_points_calc::numeric, 0), 0) * 100, 2),
        CASE
            WHEN r.short_name = 'f' THEN NULL
            WHEN r.place <= COALESCE(r.dtf, 0) THEN 'dtf'
            WHEN r.place <= COALESCE(r.dtf, 0) + COALESCE(r.special, 0) THEN 'special'
            WHEN r.place <= COALESCE(r.dtf, 0) + COALESCE(r.special, 0)
                + COALESCE(r.sc, 0) THEN 'sc'
            ELSE 'nq'
        END,
        v_max_point, r.total_voters, p_result_mode
    FROM ranked r;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RAISE NOTICE 'Refreshed % results for show %; rows=%', p_result_mode, p_show_id, v_inserted;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION refresh_show_results(p_show_id bigint)
RETURNS void AS $$
BEGIN
    PERFORM refresh_show_results_for_mode(p_show_id, 'official');
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trigger_refresh_show_results_from_vote()
RETURNS TRIGGER AS $$
DECLARE
    v_show_id bigint;
    v_result_mode text;
BEGIN
    SELECT show_id, result_mode INTO v_show_id, v_result_mode
    FROM vote_set
    WHERE id = COALESCE(NEW.vote_set_id, OLD.vote_set_id);

    IF v_show_id IS NOT NULL THEN
        PERFORM refresh_show_results_for_mode(v_show_id, v_result_mode);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trigger_refresh_show_results_from_vote_set()
RETURNS TRIGGER AS $$
DECLARE
    v_show_id bigint;
    v_result_mode text;
BEGIN
    v_show_id := COALESCE(NEW.show_id, OLD.show_id);
    v_result_mode := COALESCE(NEW.result_mode, OLD.result_mode);
    IF v_show_id IS NOT NULL THEN
        PERFORM refresh_show_results_for_mode(v_show_id, v_result_mode);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trigger_refresh_show_results_from_song_show()
RETURNS TRIGGER AS $$
DECLARE
    v_show_id bigint;
BEGIN
    v_show_id := COALESCE(NEW.show_id, OLD.show_id);
    IF v_show_id IS NOT NULL THEN
        PERFORM refresh_show_results_for_mode(v_show_id, 'official');
        IF EXISTS (SELECT 1 FROM vote_set WHERE show_id = v_show_id AND result_mode = 'revote') THEN
            PERFORM refresh_show_results_for_mode(v_show_id, 'revote');
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
