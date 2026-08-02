ALTER TABLE song_data
    ADD COLUMN previous_revision_id bigint;

-- Before moves existed, revision identity was inferred from the entry tuple.
-- Recreate those historical chains once, in deterministic revision order.
WITH lineage AS (
    SELECT id,
           LAG(id) OVER (
               PARTITION BY country_id, year_id, entry_number
               ORDER BY created_at, id
           ) AS previous_revision_id
    FROM song_data
)
UPDATE song_data AS data
SET previous_revision_id = lineage.previous_revision_id
FROM lineage
WHERE lineage.id = data.id
  AND lineage.previous_revision_id IS NOT NULL;

ALTER TABLE song_data
    ADD CONSTRAINT song_data_previous_revision_fk
        FOREIGN KEY (previous_revision_id)
        REFERENCES song_data (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT song_data_previous_revision_unique
        UNIQUE (previous_revision_id),
    ADD CONSTRAINT song_data_previous_revision_not_self
        CHECK (previous_revision_id IS DISTINCT FROM id);

CREATE OR REPLACE FUNCTION set_song_data_entry_tuple()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.song_id IS NOT NULL THEN
        SELECT country_id, year_id, entry_number
        INTO NEW.country_id, NEW.year_id, NEW.entry_number
        FROM song WHERE id = NEW.song_id
        FOR UPDATE;

        IF NEW.previous_revision_id IS NULL THEN
            SELECT id INTO NEW.previous_revision_id
            FROM song_data
            WHERE song_id = NEW.song_id
               OR (
                   song_id IS NULL
                   AND country_id = NEW.country_id
                   AND year_id = NEW.year_id
                   AND entry_number IS NOT DISTINCT FROM NEW.entry_number
               )
            ORDER BY created_at DESC, id DESC
            LIMIT 1;
        END IF;
    ELSIF NEW.previous_revision_id IS NULL THEN
        SELECT id INTO NEW.previous_revision_id
        FROM song_data
        WHERE country_id = NEW.country_id
          AND year_id = NEW.year_id
          AND entry_number IS NOT DISTINCT FROM NEW.entry_number
        ORDER BY created_at DESC, id DESC
        LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE VIEW song_change AS
WITH compared AS (
    SELECT data.*,
           TO_JSONB(data) AS current_row,
           TO_JSONB(previous) AS previous_row
    FROM song_data AS data
    LEFT JOIN song_data AS previous ON previous.id = data.previous_revision_id
), values_compared AS (
    SELECT compared.*,
           current_row - ARRAY[
               'id', 'previous_revision_id', 'song_id', 'country_id', 'year_id',
               'entry_number', 'created_at', 'modified_at', 'changed_by'
           ] AS current_values,
           previous_row - ARRAY[
               'id', 'previous_revision_id', 'song_id', 'country_id', 'year_id',
               'entry_number', 'created_at', 'modified_at', 'changed_by'
           ] AS previous_values
    FROM compared
)
SELECT values_compared.id,
       CASE
           WHEN previous_revision_id IS NULL THEN 'create'
           WHEN (previous_row ->> 'title' IS NULL
                  OR previous_row ->> 'artist' IS NULL)
             AND title IS NOT NULL AND artist IS NOT NULL THEN 'create'
           WHEN title IS NULL OR artist IS NULL THEN 'delete'
           WHEN LOWER(previous_row ->> 'title')
                    IS DISTINCT FROM LOWER(title)
             OR LOWER(previous_row ->> 'artist')
                    IS DISTINCT FROM LOWER(artist)
               THEN 'song_replacement'
           WHEN (previous_row ->> 'submitter_id')
                    IS DISTINCT FROM submitter_id::text
               THEN 'ownership_change'
           ELSE 'song_modification'
       END AS event_type,
       changed_by,
       created_at AS changed_at,
       COALESCE(song_id, (previous_row ->> 'song_id')::bigint) AS song_id,
       CASE WHEN title IS NULL OR artist IS NULL
            THEN previous_row ->> 'title' ELSE title END AS song_title,
       CASE WHEN title IS NULL OR artist IS NULL
            THEN previous_row ->> 'artist' ELSE artist END AS song_artist,
       country_id AS song_country_id,
       year_id AS song_year_id,
       CASE WHEN previous_revision_id IS NULL OR title IS NULL OR artist IS NULL
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
