CREATE TABLE country_year_results (
    country_id text NOT NULL REFERENCES country (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    country_name text NOT NULL,
    year_id integer REFERENCES year (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    song_id bigint NOT NULL REFERENCES song (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    place integer NOT NULL,
    total_countries integer NOT NULL,
    placement_percentage numeric(6, 3) NOT NULL
);

CREATE OR REPLACE FUNCTION compute_country_year_results()
RETURNS trigger AS $$
BEGIN
  IF NEW.closed = 1 AND OLD.closed IS DISTINCT FROM 1 THEN
    DELETE FROM country_year_results WHERE year_id = NEW.id;

    WITH finals AS (
      SELECT
        song_id,
        year_id,
        place AS f_place,
        total_countries AS f_total_countries,
        placement_percentage AS f_place_pct
      FROM country_show_points
      WHERE short_name = 'f' AND year_id = NEW.id
    ),
    sc AS (
      SELECT
        song_id,
        year_id,
        place AS sc_place,
        total_countries AS sc_total_countries,
        placement_percentage AS sc_place_pct
      FROM country_show_points
      WHERE short_name = 'sc' AND year_id = NEW.id
    ),
    semis_raw AS (
      SELECT
        csp.song_id,
        csp.year_id,
        csp.show_id,
        csp.short_name,
        csp.points_percentage,
        csp.placement_percentage AS sf_place_pct
      FROM country_show_points csp
      WHERE (csp.short_name LIKE 'sf%' OR csp.short_name = 'sf')
        AND csp.year_id = NEW.id
    ),
    semis AS (
      SELECT
        sr.*,
        COALESCE(NULLIF(regexp_replace(sr.short_name, '\D', '', 'g'), '')::int, 0) AS sf_index
      FROM semis_raw sr
    ),
    semis_with_ro AS (
      SELECT s.*, ss.running_order
      FROM semis s
      LEFT JOIN song_show ss
        ON ss.song_id = s.song_id AND ss.show_id = s.show_id
    ),
    base AS (
      SELECT DISTINCT country_id, country_name, song_id, year_id
      FROM country_show_points
      WHERE year_id = NEW.id
    ),
    rank_space AS (
      SELECT
        b.country_id,
        b.country_name,
        b.year_id,
        b.song_id,
        CASE
          WHEN f.song_id IS NOT NULL THEN 1
          WHEN scx.song_id IS NOT NULL THEN 2
          WHEN sro.song_id IS NOT NULL THEN 3
          ELSE 4
        END AS tier,
        f.f_place,
        f.f_place_pct,
        scx.sc_place,
        scx.sc_place_pct,
        sro.points_percentage,
        sro.sf_place_pct,
        sro.sf_index,
        sro.running_order
      FROM base b
      LEFT JOIN finals f USING (song_id, year_id)
      LEFT JOIN sc scx USING (song_id, year_id)
      LEFT JOIN semis_with_ro sro USING (song_id, year_id)
    ),
    ordered AS (
      SELECT
        rs.*,
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
      SELECT COALESCE(MAX(f_place),0) AS max_final_place FROM finals
    ),
    total_entries AS (
      SELECT COUNT(*) AS n FROM rank_space
    ),
    final_places AS (
      SELECT
        rs.country_id,
        rs.country_name,
        rs.year_id,
        rs.song_id,
        rs.f_place AS place
      FROM rank_space rs
      WHERE rs.tier = 1

      UNION ALL

      SELECT
        o.country_id,
        o.country_name,
        o.year_id,
        o.song_id,
        (SELECT max_final_place FROM max_final) + o.rn_nonfinal AS place
      FROM ordered o
    ),
    totals AS (
      SELECT (SELECT n FROM total_entries) AS total_countries
    )
    INSERT INTO country_year_results (country_id, country_name, year_id, song_id, place, total_countries, placement_percentage)
    SELECT
      fp.country_id,
      fp.country_name,
      fp.year_id,
      fp.song_id,
      fp.place,
      t.total_countries,
      CASE
        WHEN t.total_countries = 1 THEN 100
        ELSE ROUND(((t.total_countries - fp.place)::numeric / (t.total_countries - 1)::numeric) * 100, 3)
      END
    FROM final_places fp CROSS JOIN totals t
    ORDER BY fp.place;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_compute_country_year_results
AFTER UPDATE OF closed ON year
FOR EACH ROW
WHEN (NEW.closed = 1 AND OLD.closed IS DISTINCT FROM 1)
EXECUTE FUNCTION compute_country_year_results();