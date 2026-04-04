BEGIN;

-- ── 1. Drop old broken trigger and its wrapper function ───────────────
DROP TRIGGER IF EXISTS refresh_points_on_show_full ON show;
DROP FUNCTION IF EXISTS trigger_refresh_show_points();

-- ── 2. Rename table and constraints ──────────────────────────────────
ALTER TABLE country_show_points RENAME TO country_show_results;
ALTER TABLE country_show_results RENAME CONSTRAINT pk_country_show_points TO pk_country_show_results;

-- Indexes may not exist if they were dropped by the v3 migration (which
-- dropped and recreated the table without recreating the indexes).
DROP INDEX IF EXISTS idx_csp_show_id;
DROP INDEX IF EXISTS idx_csp_year_id;
DROP INDEX IF EXISTS idx_csp_country_id;
DROP INDEX IF EXISTS idx_csp_show_place;
DROP INDEX IF EXISTS idx_csp_point_distribution;

-- ── 3. Add missing / new columns ─────────────────────────────────────
-- running_order was dropped in v3; restore it
ALTER TABLE country_show_results ADD COLUMN IF NOT EXISTS running_order integer;
-- max_pts: highest single-vote score for the show's point system
ALTER TABLE country_show_results ADD COLUMN IF NOT EXISTS max_pts integer;
-- total_voters: count of voters used in the refresh calculation
ALTER TABLE country_show_results ADD COLUMN IF NOT EXISTS total_voters integer;

-- ── 4. Recreate indexes under new names ──────────────────────────────
CREATE INDEX idx_csr_show_id           ON country_show_results (show_id);
CREATE INDEX idx_csr_year_id           ON country_show_results (year_id);
CREATE INDEX idx_csr_country_id        ON country_show_results (country_id);
CREATE INDEX idx_csr_show_place        ON country_show_results (show_id, place);
CREATE INDEX idx_csr_point_distribution ON country_show_results USING gin (point_distribution);

-- ── 5. Create refresh_show_results ───────────────────────────────────
--
-- Replaces refresh_show_points.  Key fixes over the previous migrations:
--
--   • No submitter exclusion — all vote_sets for the show are counted,
--     matching the original Python get_votes_for_song() behaviour.
--
--   • Aggregation is per SONG (not per country) so that shows where a
--     country has multiple entries (specials) are handled correctly.
--     point_counts groups by song_id and aggregated joins on song_id.
--
--   • Historical self-vote rules (pre-1979) are retained as a property
--     of the song receiving votes, not the voter's identity.

CREATE OR REPLACE FUNCTION refresh_show_results(p_show_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_max_point integer;
  v_inserted  integer;
BEGIN
  SELECT MAX(p.score)
  INTO v_max_point
  FROM show sh
  JOIN point p ON p.point_system_id = sh.point_system_id
  WHERE sh.id = p_show_id;

  DELETE FROM country_show_results WHERE show_id = p_show_id;

  WITH si AS (
    SELECT sh.id, sh.year_id, sh.dtf, sh.sc, sh.special,
           sh.short_name, sh.show_name
    FROM show sh
    WHERE sh.id = p_show_id
  ),
  -- All vote sets for this show; no submitter exclusion
  all_vote_sets AS (
    SELECT vs.*
    FROM vote_set vs
    WHERE vs.show_id = p_show_id
  ),
  -- Used only for historical max_possible_points (self-vote exclusions)
  voters_by_country AS (
    SELECT vs.country_id, COUNT(*) AS cnt
    FROM all_vote_sets vs
    WHERE vs.country_id IS NOT NULL
    GROUP BY vs.country_id
  ),
  totals AS (
    SELECT COUNT(*) AS total_valid FROM all_vote_sets
  ),
  -- Vote counts keyed by SONG so specials with multiple entries from the
  -- same country are tallied independently
  point_counts AS (
    SELECT s.id AS song_id, s.country_id, v.score, COUNT(*) AS cnt
    FROM si
    JOIN song_show ss ON ss.show_id = si.id
    JOIN song s        ON s.id = ss.song_id
    JOIN vote v        ON v.song_id = s.id
    JOIN all_vote_sets vs ON vs.id = v.vote_set_id
    WHERE
      -- Modern rules (1979+): all votes count
      (si.year_id IS NULL OR si.year_id >= 1979)
      -- 1965–1978: a country cannot vote for its own entry
      OR (si.year_id BETWEEN 1965 AND 1978
          AND vs.country_id IS DISTINCT FROM s.country_id)
      -- Pre-1965 (except 1960): also no self-votes
      OR (si.year_id < 1965 AND si.year_id <> 1960
          AND vs.country_id IS DISTINCT FROM s.country_id)
      -- 1960 special rule: self-vote allowed only if score = 1
      OR (si.year_id = 1960 AND (
            vs.country_id IS DISTINCT FROM s.country_id
            OR (vs.country_id = s.country_id AND v.score = 1)
          ))
    GROUP BY s.id, s.country_id, v.score
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
      si.dtf, si.sc, si.special,
      COALESCE(SUM(pc.score * pc.cnt), 0) AS total_points,
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
        WHEN si.year_id IS NULL OR si.year_id >= 1979
          THEN v_max_point * (SELECT total_valid FROM totals)
        WHEN (si.year_id BETWEEN 1966 AND 1978)
            OR (si.year_id = 1965 AND si.short_name = 'f')
          THEN v_max_point * GREATEST(
                 (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0), 0)
        WHEN si.year_id = 1960
          THEN v_max_point * GREATEST(
                 (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0), 0)
               + COALESCE(vbc.cnt, 0)
        ELSE
          v_max_point * GREATEST(
            (SELECT total_valid FROM totals) - COALESCE(vbc.cnt, 0), 0)
      END AS max_possible_points_calc
    FROM si
    JOIN song_show ss ON ss.show_id = si.id
    JOIN song s        ON s.id = ss.song_id
    JOIN country c     ON c.id = s.country_id
    -- Join on song_id so each song gets only its own vote counts
    LEFT JOIN point_counts pc       ON pc.song_id = s.id
    LEFT JOIN voters_by_country vbc ON vbc.country_id = s.country_id
    WHERE c.is_participating
    GROUP BY
      s.country_id, c.name, si.id, s.id, ss.running_order,
      si.show_name, si.short_name, si.year_id,
      si.dtf, si.sc, si.special, vbc.cnt
  ),
  ranked AS (
    SELECT
      a.*,
      DENSE_RANK() OVER (
        ORDER BY total_points DESC, total_votes_received DESC, countback_string DESC
      ) AS place,
      COUNT(*) OVER () AS total_countries
    FROM aggregated a
  )
  INSERT INTO country_show_results (
    country_id, country_name, show_id, show_name, short_name, year_id,
    song_id, running_order,
    total_points, total_votes_received, point_distribution,
    place, total_countries, placement_percentage,
    max_possible_points, points_percentage,
    entry_status, max_pts, total_voters
  )
  SELECT
    r.country_id,
    r.country_name,
    r.show_id,
    r.show_name,
    r.short_name,
    r.year_id,
    r.song_id,
    r.running_order,
    r.total_points,
    r.total_votes_received,
    r.point_distribution,
    r.place,
    r.total_countries,
    ROUND(
      CASE
        WHEN r.place = r.total_countries THEN 0
        WHEN r.total_countries > 1
          THEN ((r.total_countries - r.place)::numeric
                / (r.total_countries - 1)) * 100
        ELSE 100
      END, 2
    ) AS placement_percentage,
    COALESCE(r.max_possible_points_calc, 0) AS max_possible_points,
    ROUND(
      COALESCE(
        r.total_points::numeric / NULLIF(r.max_possible_points_calc::numeric, 0),
        0
      ) * 100, 2
    ) AS points_percentage,
    CASE
      WHEN r.short_name = 'f' THEN NULL
      WHEN r.place <= COALESCE(r.dtf, 0) THEN 'dtf'
      WHEN r.place <= COALESCE(r.dtf, 0) + COALESCE(r.special, 0) THEN 'special'
      WHEN r.place <= COALESCE(r.dtf, 0) + COALESCE(r.special, 0)
                    + COALESCE(r.sc, 0) THEN 'sc'
      ELSE 'nq'
    END AS entry_status,
    v_max_point AS max_pts,
    r.total_voters
  FROM ranked r;

  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  RAISE NOTICE 'Refreshed results for show %; rows=%', p_show_id, v_inserted;
END;
$$;

-- ── 6. Drop old refresh_show_points function ──────────────────────────
DROP FUNCTION IF EXISTS refresh_show_points(bigint);

-- ── 7. Trigger function: vote row changes ─────────────────────────────
CREATE OR REPLACE FUNCTION trigger_refresh_show_results_from_vote()
RETURNS TRIGGER AS $$
DECLARE
  v_show_id integer;
BEGIN
  SELECT show_id INTO v_show_id
  FROM vote_set
  WHERE id = COALESCE(NEW.vote_set_id, OLD.vote_set_id);

  IF v_show_id IS NOT NULL THEN
    PERFORM refresh_show_results(v_show_id);
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- ── 8. Trigger function: vote_set row changes ─────────────────────────
CREATE OR REPLACE FUNCTION trigger_refresh_show_results_from_vote_set()
RETURNS TRIGGER AS $$
DECLARE
  v_show_id integer;
BEGIN
  v_show_id := COALESCE(NEW.show_id, OLD.show_id);
  IF v_show_id IS NOT NULL THEN
    PERFORM refresh_show_results(v_show_id);
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- ── 9. Triggers on vote and vote_set ──────────────────────────────────
CREATE TRIGGER trg_refresh_show_results_on_vote
  AFTER INSERT OR UPDATE OR DELETE ON vote
  FOR EACH ROW
  EXECUTE FUNCTION trigger_refresh_show_results_from_vote();

CREATE TRIGGER trg_refresh_show_results_on_vote_set
  AFTER INSERT OR UPDATE OR DELETE ON vote_set
  FOR EACH ROW
  EXECUTE FUNCTION trigger_refresh_show_results_from_vote_set();

-- ── 10. Update compute_country_year_results to use new table name ─────
CREATE OR REPLACE FUNCTION compute_country_year_results()
RETURNS trigger AS $$
BEGIN
  IF NEW.closed = 1 AND OLD.closed IS DISTINCT FROM 1 THEN
    DELETE FROM country_year_results WHERE year_id = NEW.id;

    WITH finals AS (
      SELECT song_id, year_id, place AS f_place,
             total_countries AS f_total_countries,
             placement_percentage AS f_place_pct
      FROM country_show_results
      WHERE short_name = 'f' AND year_id = NEW.id
    ),
    sc AS (
      SELECT song_id, year_id, place AS sc_place,
             total_countries AS sc_total_countries,
             placement_percentage AS sc_place_pct
      FROM country_show_results
      WHERE short_name = 'sc' AND year_id = NEW.id
    ),
    semis_raw AS (
      SELECT csr.song_id, csr.year_id, csr.show_id, csr.short_name,
             csr.points_percentage, csr.placement_percentage AS sf_place_pct
      FROM country_show_results csr
      WHERE (csr.short_name LIKE 'sf%' OR csr.short_name = 'sf')
        AND csr.year_id = NEW.id
    ),
    semis AS (
      SELECT sr.*,
             COALESCE(NULLIF(regexp_replace(sr.short_name, '\D', '', 'g'), '')::int, 0) AS sf_index
      FROM semis_raw sr
    ),
    semis_with_ro AS (
      SELECT s.*, ss.running_order
      FROM semis s
      LEFT JOIN song_show ss ON ss.song_id = s.song_id AND ss.show_id = s.show_id
    ),
    base AS (
      SELECT DISTINCT country_id, country_name, song_id, year_id
      FROM country_show_results
      WHERE year_id = NEW.id
    ),
    rank_space AS (
      SELECT
        b.country_id, b.country_name, b.year_id, b.song_id,
        CASE
          WHEN f.song_id IS NOT NULL  THEN 1
          WHEN scx.song_id IS NOT NULL THEN 2
          WHEN sro.song_id IS NOT NULL THEN 3
          ELSE 4
        END AS tier,
        f.f_place, f.f_place_pct,
        scx.sc_place, scx.sc_place_pct,
        sro.points_percentage, sro.sf_place_pct, sro.sf_index, sro.running_order
      FROM base b
      LEFT JOIN finals f       USING (song_id, year_id)
      LEFT JOIN sc scx         USING (song_id, year_id)
      LEFT JOIN semis_with_ro sro USING (song_id, year_id)
    ),
    ordered AS (
      SELECT rs.*,
             ROW_NUMBER() OVER (
               ORDER BY
                 CASE WHEN rs.tier=2 THEN 0 WHEN rs.tier=3 THEN 1 ELSE 2 END,
                 CASE WHEN rs.tier=2 THEN rs.sc_place END,
                 CASE WHEN rs.tier=3 THEN rs.points_percentage END DESC,
                 CASE WHEN rs.tier=3 THEN rs.sf_place_pct END,
                 CASE WHEN rs.tier=3 THEN rs.sf_index END,
                 CASE WHEN rs.tier=3 THEN rs.running_order END
             ) AS rn_nonfinal
      FROM rank_space rs
      WHERE rs.tier IN (2,3)
    ),
    max_final AS (
      SELECT COALESCE(MAX(f_place), 0) AS max_final_place FROM finals
    ),
    total_entries AS (
      SELECT COUNT(*) AS n FROM rank_space
    ),
    final_places AS (
      SELECT rs.country_id, rs.country_name, rs.year_id, rs.song_id,
             rs.f_place AS place
      FROM rank_space rs WHERE rs.tier = 1

      UNION ALL

      SELECT o.country_id, o.country_name, o.year_id, o.song_id,
             (SELECT max_final_place FROM max_final) + o.rn_nonfinal AS place
      FROM ordered o
    ),
    totals AS (
      SELECT (SELECT n FROM total_entries) AS total_countries
    )
    INSERT INTO country_year_results
      (country_id, country_name, year_id, song_id, place, total_countries, placement_percentage)
    SELECT
      fp.country_id, fp.country_name, fp.year_id, fp.song_id, fp.place,
      t.total_countries,
      CASE
        WHEN t.total_countries = 1 THEN 100
        ELSE ROUND(
               ((t.total_countries - fp.place)::numeric
                / (t.total_countries - 1)::numeric) * 100, 3)
      END
    FROM final_places fp CROSS JOIN totals t
    ORDER BY fp.place;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
