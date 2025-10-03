BEGIN;

DROP TABLE country_show_points;
CREATE TABLE country_show_points (
    country_id text NOT NULL REFERENCES country (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    country_name text NOT NULL,
    show_id integer NOT NULL REFERENCES show (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    show_name text,
    short_name text,
    year_id integer REFERENCES year (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    song_id bigint NOT NULL REFERENCES song (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    total_points integer NOT NULL,
    total_votes_received integer NOT NULL,
    point_distribution jsonb NOT NULL,
    place integer NOT NULL,
    total_countries integer NOT NULL,
    placement_percentage numeric(5,2) NOT NULL,
    max_possible_points integer NOT NULL,
    points_percentage numeric(5,2) NOT NULL,
    entry_status text,
    calculated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_country_show_points PRIMARY KEY (song_id, show_id)
);

CREATE OR REPLACE FUNCTION refresh_show_points(p_show_id bigint)
RETURNS void AS $$
DECLARE
    v_max_point integer;
    v_total_countries integer;
    v_total_voters integer;
    v_dtf integer;
    v_sc integer;
    v_special integer;
BEGIN
    SELECT MAX(p.score)
    INTO v_max_point
    FROM show sh
    JOIN point p ON p.point_system_id = sh.point_system_id
    WHERE sh.id = p_show_id;

    SELECT COALESCE(dtf, 0), COALESCE(sc, 0), COALESCE(special, 0)
    INTO v_dtf, v_sc, v_special
    FROM show sh
    WHERE sh.id = p_show_id;

    SELECT COUNT(DISTINCT vs.id) INTO v_total_voters
    FROM vote_set vs
    WHERE vs.show_id = p_show_id;

    DELETE FROM country_show_points WHERE show_id = p_show_id;

    WITH point_counts AS (
        SELECT
            s.country_id,
            sh.id AS show_id,
            score,
            COUNT(*) AS count
        FROM show sh
        JOIN song s ON s.year_id = sh.year_id
        JOIN song_show ss ON ss.song_id = s.id AND ss.show_id = sh.id
        JOIN vote v ON v.song_id = s.id
        JOIN vote_set vs ON vs.id = v.vote_set_id AND vs.show_id = sh.id
        WHERE sh.id = p_show_id
        GROUP BY s.country_id, sh.id, score
    ),
    song_details AS (
        SELECT
            ss.song_id,
            ss.show_id,
            s.country_id
        FROM song_show ss
        JOIN song s ON ss.song_id = s.id
        WHERE ss.show_id = p_show_id
    ),
    aggregated AS (
        SELECT
            s.country_id,
            c.name AS country_name,
            ss.show_id,
            sh.show_name,
            sh.short_name,
            sh.year_id,
            COALESCE(SUM(pc.score * pc.count), 0) AS total_points,
            COALESCE(SUM(pc.count), 0) AS total_votes_received,
            COALESCE(
                JSONB_OBJECT_AGG(
                    pc.score::text,
                    pc.count
                    ORDER BY pc.score DESC
                ) FILTER (WHERE pc.score IS NOT NULL),
                '{}'::jsonb
            ) AS point_distribution,
            COALESCE(
                STRING_AGG(
                    LPAD(pc.score::text, 3, '0') || ':' || LPAD(pc.count::text, 3, '0'),
                    ',' ORDER BY pc.score DESC
                ) FILTER (WHERE pc.score IS NOT NULL),
                ''
            ) AS countback_string
        FROM song s
        JOIN country c ON c.id = s.country_id
        JOIN song_show ss ON ss.song_id = s.id
        JOIN show sh ON sh.id = ss.show_id
        LEFT JOIN point_counts pc ON pc.country_id = s.country_id AND pc.show_id = ss.show_id
        WHERE c.is_participating
          AND ss.show_id = p_show_id
        GROUP BY
            s.country_id,
            c.name,
            ss.show_id,
            sh.show_name,
            sh.short_name,
            sh.year_id
    ),
    ranked AS (
        SELECT
            *,
            DENSE_RANK() OVER (
                ORDER BY
                    total_points DESC,
                    total_votes_received DESC,
                    countback_string DESC
            ) AS place,
            COUNT(*) OVER () AS total_countries
        FROM aggregated
    )
    INSERT INTO country_show_points (
        country_id,
        country_name,
        show_id,
        show_name,
        short_name,
        year_id,
        song_id,
        total_points,
        total_votes_received,
        point_distribution,
        place,
        total_countries,
        placement_percentage,
        max_possible_points,
        points_percentage,
        entry_status
    )
    SELECT
        r.country_id,
        country_name,
        r.show_id,
        show_name,
        short_name,
        year_id,
        song_id,
        total_points,
        total_votes_received,
        point_distribution,
        place,
        total_countries,
        ROUND(
            CASE
                WHEN total_countries > 1
                THEN ((total_countries - place)::numeric / (total_countries - 1)) * 100
                ELSE 100
            END, 2
        ) AS placement_percentage,
        v_max_point * v_total_voters AS max_possible_points,
        ROUND(
            CASE
                WHEN v_total_voters > 0 AND v_max_point IS NOT NULL
                THEN (total_points::numeric / (v_max_point * v_total_voters)) * 100
                ELSE 0
            END, 2
        ) AS points_percentage,
        CASE
            WHEN place <= v_dtf THEN 'dtf'
            WHEN v_dtf < place AND place <= v_dtf + v_special + v_sc THEN 'sc'
            ELSE 'nq'
        END AS entry_status
    FROM ranked r
    JOIN song_details sd ON sd.show_id = r.show_id AND sd.country_id = r.country_id;
END;
$$ LANGUAGE plpgsql;

COMMIT;