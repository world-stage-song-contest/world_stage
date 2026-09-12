BEGIN;

CREATE FUNCTION normalize_national_final_entry_numbers(target_year bigint, target_country text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    selected_id bigint;
    next_number integer;
BEGIN
    IF target_year < 0 THEN
        RETURN;
    END IF;

    PERFORM id FROM song
    WHERE year_id = target_year AND country_id = target_country
    ORDER BY id FOR UPDATE;

    SELECT id INTO selected_id FROM song
    WHERE year_id = target_year AND country_id = target_country AND main_participant
    ORDER BY id LIMIT 1;

    SELECT GREATEST(COALESCE(MAX(entry_number), 1), 1) + 1 INTO next_number
    FROM song WHERE year_id = target_year AND country_id = target_country;

    UPDATE song SET entry_number = next_number
    WHERE year_id = target_year AND country_id = target_country
      AND entry_number = 1 AND id IS DISTINCT FROM selected_id;

    UPDATE song SET entry_number = 1
    WHERE id = selected_id AND entry_number <> 1;
END;
$$;

SELECT normalize_national_final_entry_numbers(year_id, country_id)
FROM (
    SELECT DISTINCT song.year_id, song.country_id
    FROM song
    JOIN national_final_song ON national_final_song.song_id = song.id
    WHERE song.year_id >= 0
) AS slots;

COMMIT;
