ALTER TABLE song ADD COLUMN entry_code text;

CREATE OR REPLACE VIEW current_song AS
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
       data.time_signature_set_id, data.artist_credit_set_id, song.entry_code
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
