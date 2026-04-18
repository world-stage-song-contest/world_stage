BEGIN;

CREATE INDEX IF NOT EXISTS idx_song_language_language_id ON song_language (language_id);
CREATE INDEX IF NOT EXISTS idx_vote_song_id ON vote (song_id);

COMMIT;