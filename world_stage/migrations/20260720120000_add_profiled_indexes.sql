BEGIN;

-- Show routes and administrative updates identify a show by this pair. The
-- unique index also makes that application-level identity explicit.
CREATE UNIQUE INDEX IF NOT EXISTS show_year_short_name_key
    ON show (year_id, short_name);

-- Winner lookups filter on all four columns. The existing single-column year
-- index and show-oriented result indexes cannot narrow this access path well.
CREATE INDEX IF NOT EXISTS idx_csr_year_short_mode_place
    ON country_show_results (year_id, short_name, result_mode, place);

-- country_year_results previously had no indexes despite being read both by
-- year/place and by song.
CREATE INDEX IF NOT EXISTS idx_cyr_year_place
    ON country_year_results (year_id, place);
CREATE INDEX IF NOT EXISTS idx_cyr_song_id
    ON country_year_results (song_id);

-- The uniqueness constraints lead with user_id and set_id respectively, so
-- they do not support reverse show/song lookups or their foreign-key checks.
CREATE INDEX IF NOT EXISTS idx_prediction_set_show_id
    ON prediction_set (show_id);
CREATE INDEX IF NOT EXISTS idx_prediction_song_id
    ON prediction (song_id);

COMMIT;
