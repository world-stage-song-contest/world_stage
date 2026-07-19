BEGIN;

-- Exact duplicates left by the original SQLite import. Keep the stable or
-- unique version of each access path.
DROP INDEX IF EXISTS idx_25550517_user_username_index;
DROP INDEX IF EXISTS idx_25550562_idx_song_country;
DROP INDEX IF EXISTS idx_25550562_idx_song_id_country_id;
DROP INDEX IF EXISTS idx_25550504_idx_point_system_id_score;

-- These duplicate a UNIQUE index on the same columns.
DROP INDEX IF EXISTS idx_api_token_hash;
DROP INDEX IF EXISTS song_key_signature_song_idx;
DROP INDEX IF EXISTS song_time_signature_song_idx;
DROP INDEX IF EXISTS idx_song_show_song_show;
DROP INDEX IF EXISTS idx_25550549_idx_song_show_song_show;

-- These single-column or shorter indexes are left prefixes of retained
-- constraint or workload indexes.
DROP INDEX IF EXISTS subgenre_genre_idx;
DROP INDEX IF EXISTS song_subgenre_song_idx;
DROP INDEX IF EXISTS idx_scrobble_account_user_id;
DROP INDEX IF EXISTS idx_song_year;
DROP INDEX IF EXISTS idx_25550562_idx_song_year;
DROP INDEX IF EXISTS idx_vote_set_voter;
DROP INDEX IF EXISTS idx_25550524_idx_vote_set_voter;
DROP INDEX IF EXISTS idx_vote_set_show;
DROP INDEX IF EXISTS idx_25550524_idx_vote_set_show;
DROP INDEX IF EXISTS idx_vote_set_voter_show;
DROP INDEX IF EXISTS idx_csr_show_id;
DROP INDEX IF EXISTS idx_csr_year_id;

COMMIT;
