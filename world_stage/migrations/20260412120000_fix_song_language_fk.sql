-- Fix song_language.language_id FK: it wrongly references song(id)
-- instead of language(id), inherited from the SQLite→Postgres migration.

ALTER TABLE song_language
    DROP CONSTRAINT IF EXISTS song_language_language_id_fkey;

ALTER TABLE song_language
    ADD CONSTRAINT song_language_language_id_fkey
    FOREIGN KEY (language_id) REFERENCES language(id)
    ON UPDATE RESTRICT ON DELETE RESTRICT;
