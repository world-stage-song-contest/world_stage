BEGIN;

-- Rewrite compute_country_year_results with a correct three-tier ranking and
-- extract the logic into a standalone refresh_year_results(p_year_id) function
-- so that historical years can be reprocessed without re-triggering the closed
-- transition:
--
--   SELECT refresh_year_results(2024);
--
--   Tier 1 – Final entrants, placed 1…N by their Final result.
--
--   Tier 2 – Repechage entrants who did not reach the Final, placed
--             N+1…N+M ordered by their Repechage place (best non-qualifier
--             first).
--
--   Tier 3 – Semi-final entrants who reached neither the Final nor the
--             Repechage qualifying zone, placed N+M+1… ordered by:
--               1. points_percentage DESC  (normalised for voter-count
--                                           differences between semis)
--               2. total_voters DESC       (larger jury = more weight)
--               3. countback DESC          (more high-point awards wins)
--               4. running_order ASC       (earlier performance wins)
--               5. sf_index ASC            (lower semi-final number wins)
--
-- The countback string is recomputed on-the-fly from the stored
-- point_distribution JSONB so no extra column is required.

CREATE OR REPLACE FUNCTION refresh_year_results(p_year_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  DELETE FROM country_year_results WHERE year_id = p_year_id;

  WITH finals AS (
    SELECT song_id, year_id, place AS f_place
    FROM country_show_results
    WHERE short_name = 'f' AND year_id = p_year_id
  ),
  sc_entries AS (
    SELECT song_id, year_id, place AS sc_place
    FROM country_show_results
    WHERE short_name = 'sc' AND year_id = p_year_id
  ),
  sf_entries AS (
    SELECT
      csr.song_id,
      csr.year_id,
      csr.points_percentage                                          AS sf_pct,
      csr.total_voters                                               AS sf_voters,
      csr.running_order                                              AS sf_running_order,
      COALESCE(
        NULLIF(regexp_replace(csr.short_name, '\D', '', 'g'), '')::int,
        0
      )                                                              AS sf_index,
      -- Reconstruct countback string from stored point_distribution:
      -- "NNN:MMM,..." sorted by score DESC so lexicographic order = countback order
      (
        SELECT STRING_AGG(
          LPAD(j.key, 3, '0') || ':' || LPAD(j.value, 3, '0'),
          ',' ORDER BY j.key::integer DESC
        )
        FROM jsonb_each_text(csr.point_distribution) AS j
      )                                                              AS sf_countback
    FROM country_show_results csr
    WHERE (csr.short_name LIKE 'sf%' OR csr.short_name = 'sf')
      AND csr.year_id = p_year_id
  ),
  base AS (
    SELECT DISTINCT country_id, country_name, song_id, year_id
    FROM country_show_results
    WHERE year_id = p_year_id
  ),
  rank_space AS (
    SELECT
      b.country_id,
      b.country_name,
      b.year_id,
      b.song_id,
      CASE
        WHEN f.song_id  IS NOT NULL THEN 1
        WHEN sc.song_id IS NOT NULL THEN 2
        WHEN sf.song_id IS NOT NULL THEN 3
        ELSE 4
      END                    AS tier,
      f.f_place,
      sc.sc_place,
      sf.sf_pct,
      sf.sf_voters,
      sf.sf_countback,
      sf.sf_running_order,
      sf.sf_index
    FROM base b
    LEFT JOIN finals     f  ON f.song_id  = b.song_id AND f.year_id  = b.year_id
    LEFT JOIN sc_entries sc ON sc.song_id = b.song_id AND sc.year_id = b.year_id
    LEFT JOIN sf_entries sf ON sf.song_id = b.song_id AND sf.year_id = b.year_id
  ),
  -- ── Tier 1: Final results ──────────────────────────────────────────
  ranked_finals AS (
    SELECT *,
           ROW_NUMBER() OVER (ORDER BY f_place ASC) AS rank_in_tier
    FROM rank_space
    WHERE tier = 1
  ),
  -- ── Tier 2: Repechage non-qualifiers ──────────────────────────────
  ranked_sc AS (
    SELECT *,
           ROW_NUMBER() OVER (ORDER BY sc_place ASC) AS rank_in_tier
    FROM rank_space
    WHERE tier = 2
  ),
  -- ── Tier 3: Semi-final non-qualifiers ─────────────────────────────
  ranked_sf AS (
    SELECT *,
           ROW_NUMBER() OVER (
             ORDER BY
               sf_pct            DESC,
               sf_voters         DESC,
               sf_countback      DESC NULLS LAST,
               sf_running_order  ASC  NULLS LAST,
               sf_index          ASC
           ) AS rank_in_tier
    FROM rank_space
    WHERE tier = 3
  ),
  -- ── Tier 4: fallback (any entry not covered above) ────────────────
  ranked_other AS (
    SELECT *,
           ROW_NUMBER() OVER (ORDER BY song_id) AS rank_in_tier
    FROM rank_space
    WHERE tier = 4
  ),
  tier_sizes AS (
    SELECT
      (SELECT COUNT(*) FROM ranked_finals) AS n_f,
      (SELECT COUNT(*) FROM ranked_sc)     AS n_sc,
      (SELECT COUNT(*) FROM ranked_sf)     AS n_sf
  ),
  all_ranked AS (
    SELECT country_id, country_name, year_id, song_id,
           rank_in_tier::integer AS place
    FROM ranked_finals

    UNION ALL

    SELECT country_id, country_name, year_id, song_id,
           ((SELECT n_f  FROM tier_sizes) + rank_in_tier)::integer
    FROM ranked_sc

    UNION ALL

    SELECT country_id, country_name, year_id, song_id,
           ((SELECT n_f + n_sc FROM tier_sizes) + rank_in_tier)::integer
    FROM ranked_sf

    UNION ALL

    SELECT country_id, country_name, year_id, song_id,
           ((SELECT n_f + n_sc + n_sf FROM tier_sizes) + rank_in_tier)::integer
    FROM ranked_other
  ),
  totals AS (
    SELECT COUNT(*) AS total_countries FROM all_ranked
  )
  INSERT INTO country_year_results
    (country_id, country_name, year_id, song_id,
     place, total_countries, placement_percentage)
  SELECT
    ar.country_id,
    ar.country_name,
    ar.year_id,
    ar.song_id,
    ar.place,
    t.total_countries,
    CASE
      WHEN t.total_countries = 1 THEN 100
      ELSE ROUND(
             ((t.total_countries - ar.place)::numeric
              / (t.total_countries - 1)::numeric) * 100,
             3)
    END AS placement_percentage
  FROM all_ranked ar
  CROSS JOIN totals t
  ORDER BY ar.place;

END;
$$;

-- Trigger function: delegates to refresh_year_results on the closed → 1
-- transition so normal edits to a year row don't trigger a full recompute.

CREATE OR REPLACE FUNCTION compute_country_year_results()
RETURNS trigger AS $$
BEGIN
  IF NEW.closed = 1 AND OLD.closed IS DISTINCT FROM 1 THEN
    PERFORM refresh_year_results(NEW.id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
