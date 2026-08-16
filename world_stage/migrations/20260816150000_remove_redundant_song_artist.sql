CREATE OR REPLACE FUNCTION artist_credit_name(p_artist_credit_set_id bigint)
RETURNS text
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    SELECT STRING_AGG(
        COALESCE(credit.join_phrase, '')
        || COALESCE(credit.stage_name, artist.full_name),
        '' ORDER BY credit.position
    )
    FROM artist_credit AS credit
    JOIN artist ON artist.id = credit.artist_id
    WHERE credit.artist_credit_set_id = p_artist_credit_set_id;
$$;

-- SQL and PL/pgSQL function bodies do not create ordinary column
-- dependencies in PostgreSQL. Update the two result functions that read the
-- latest song revision before removing the column they previously referenced.
DO $$
DECLARE
    signature regprocedure;
    definition text;
    updated_definition text;
BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'ballot_entry_rule_matrix(bigint,text)'::regprocedure,
        'refresh_show_results_for_mode(bigint,text)'::regprocedure
    ] LOOP
        definition := PG_GET_FUNCTIONDEF(signature);
        updated_definition := REPLACE(
            REPLACE(
                definition,
                'revision.artist',
                'revision.artist_credit_set_id'
            ),
            'data.artist IS NOT NULL',
            'data.artist_credit_set_id IS NOT NULL'
        );
        IF updated_definition = definition THEN
            RAISE EXCEPTION 'Could not update obsolete artist reference in %', signature;
        END IF;
        EXECUTE updated_definition;
    END LOOP;
END;
$$;

DROP VIEW song_change;
DROP VIEW current_song;

ALTER TABLE song_data DROP COLUMN artist;

CREATE VIEW current_song AS
SELECT song.id, song.country_id, song.year_id, song.entry_number,
       data.submitter_id, data.title,
       artist_credit_name(data.artist_credit_set_id) AS artist,
       data.created_at, data.changed_by,
       data.modified_at, data.native_title, data.translated_lyrics,
       data.romanized_lyrics, data.native_lyrics, data.video_link,
       data.snippet_start, data.snippet_end, data.snippet2_start,
       data.snippet2_end, COALESCE(status.is_placeholder, false) AS is_placeholder,
       data.title_language_id,
       data.native_language_id, data.language_set_id, data.notes, data.sources,
       COALESCE(status.approval_status, 'pending') AS approval_status,
       status.created_at AS status_created_at,
       data.poster_link, data.vtt_link, data.duration, data.id AS song_data_id,
       song.main_participant, data.genre_set_id, data.key_signature_set_id,
       data.time_signature_set_id, data.artist_credit_set_id
FROM song
JOIN LATERAL (
    SELECT song_data.* FROM song_data
    WHERE song_data.song_id = song.id
       OR (
           song_data.song_id IS NULL
           AND song_data.country_id = song.country_id
           AND song_data.year_id = song.year_id
           AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
       )
    ORDER BY song_data.created_at DESC, song_data.id DESC
    LIMIT 1
) AS data ON true
LEFT JOIN LATERAL (
    SELECT song_status.approval_status, song_status.is_placeholder,
           song_status.created_at
    FROM song_status
    WHERE song_status.song_id = song.id
    ORDER BY song_status.created_at DESC, song_status.id DESC
    LIMIT 1
) AS status ON true
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL;

CREATE VIEW song_change AS
WITH compared AS (
    SELECT data.*,
           TO_JSONB(data) || JSONB_BUILD_OBJECT(
               'artist', artist_credit_name(data.artist_credit_set_id)
           ) AS current_row,
           TO_JSONB(previous) || JSONB_BUILD_OBJECT(
               'artist', artist_credit_name(previous.artist_credit_set_id)
           ) AS previous_row
    FROM song_data AS data
    LEFT JOIN song_data AS previous ON previous.id = data.previous_revision_id
), values_compared AS (
    SELECT compared.*,
           current_row - ARRAY[
               'id', 'previous_revision_id', 'song_id', 'country_id', 'year_id',
               'entry_number', 'created_at', 'modified_at', 'changed_by',
               'artist_credit_set_id'
           ] AS current_values,
           previous_row - ARRAY[
               'id', 'previous_revision_id', 'song_id', 'country_id', 'year_id',
               'entry_number', 'created_at', 'modified_at', 'changed_by',
               'artist_credit_set_id'
           ] AS previous_values
    FROM compared
)
SELECT values_compared.id,
       CASE
           WHEN previous_revision_id IS NULL THEN 'create'
           WHEN (previous_row ->> 'title' IS NULL
                  OR previous_row ->> 'artist' IS NULL)
             AND title IS NOT NULL AND artist_credit_set_id IS NOT NULL THEN 'create'
           WHEN title IS NULL OR artist_credit_set_id IS NULL THEN 'delete'
           WHEN LOWER(previous_row ->> 'title')
                    IS DISTINCT FROM LOWER(title)
             OR LOWER(previous_row ->> 'artist')
                    IS DISTINCT FROM LOWER(current_row ->> 'artist')
               THEN 'song_replacement'
           WHEN (previous_row ->> 'submitter_id')
                    IS DISTINCT FROM submitter_id::text
               THEN 'ownership_change'
           ELSE 'song_modification'
       END AS event_type,
       changed_by,
       created_at AS changed_at,
       COALESCE(song_id, (previous_row ->> 'song_id')::bigint) AS song_id,
       CASE WHEN title IS NULL OR artist_credit_set_id IS NULL
            THEN previous_row ->> 'title' ELSE title END AS song_title,
       CASE WHEN title IS NULL OR artist_credit_set_id IS NULL
            THEN previous_row ->> 'artist' ELSE current_row ->> 'artist' END AS song_artist,
       country_id AS song_country_id,
       year_id AS song_year_id,
       CASE WHEN previous_revision_id IS NULL OR title IS NULL
                 OR artist_credit_set_id IS NULL
            THEN NULL ELSE fields.changed_fields END AS changed_fields
FROM values_compared
LEFT JOIN LATERAL (
    SELECT JSONB_OBJECT_AGG(
               field.key,
               JSONB_BUILD_OBJECT(
                   'old', previous_values -> field.key,
                   'new', current_values -> field.key
               )
           ) AS changed_fields
    FROM JSONB_EACH(current_values) AS field
    WHERE previous_values -> field.key IS DISTINCT FROM field.value
) AS fields ON true;
