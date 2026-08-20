CREATE OR REPLACE VIEW song_change AS
WITH artist_names AS MATERIALIZED (
    SELECT credit.artist_credit_set_id,
           STRING_AGG(
               COALESCE(credit.join_phrase, '')
               || COALESCE(credit.stage_name, artist.full_name),
               '' ORDER BY credit.position
           ) AS name
    FROM artist_credit AS credit
    JOIN artist ON artist.id = credit.artist_id
    GROUP BY credit.artist_credit_set_id
), compared AS (
    SELECT data.*,
           TO_JSONB(data) || JSONB_BUILD_OBJECT(
               'artist', current_artist.name
           ) AS current_row,
           TO_JSONB(previous) || JSONB_BUILD_OBJECT(
               'artist', previous_artist.name
           ) AS previous_row
    FROM song_data AS data
    LEFT JOIN song_data AS previous ON previous.id = data.previous_revision_id
    LEFT JOIN artist_names AS current_artist
      ON current_artist.artist_credit_set_id = data.artist_credit_set_id
    LEFT JOIN artist_names AS previous_artist
      ON previous_artist.artist_credit_set_id = previous.artist_credit_set_id
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
