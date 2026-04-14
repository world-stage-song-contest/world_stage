-- Fix refresh_show_results so specials (negative year_id) use modern voting rules.
-- Previously, year_id < 1965 triggered pre-1965 rules (excluding same-country votes),
-- which broke the totals for specials that have negative IDs.

BEGIN;

CREATE OR REPLACE FUNCTION refresh_show_results(p_show_id bigint)
RETURNS void AS $$
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
  all_vote_sets AS (
    SELECT vs.*
    FROM vote_set vs
    WHERE vs.show_id = p_show_id
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
    JOIN song s        ON s.id = ss.song_id
    JOIN vote v        ON v.song_id = s.id
    JOIN all_vote_sets vs ON vs.id = v.vote_set_id
    WHERE
      -- Modern rules (1979+) and specials (negative year_id): all votes count
      (si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979)
      -- 1965–1978: a country cannot vote for its own entry
      OR (si.year_id BETWEEN 1965 AND 1978
          AND vs.country_id IS DISTINCT FROM s.country_id)
      -- Pre-1965 (except 1960): also no self-votes
      OR (si.year_id >= 0 AND si.year_id < 1965 AND si.year_id <> 1960
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
        WHEN si.year_id IS NULL OR si.year_id < 0 OR si.year_id >= 1979
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
$$ LANGUAGE plpgsql;

-- Re-run the refresh for all special shows so their results table is corrected
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT id FROM show WHERE year_id < 0 LOOP
    PERFORM refresh_show_results(r.id);
  END LOOP;
END $$;

COMMIT;
