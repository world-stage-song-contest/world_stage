BEGIN TRANSACTION;

CREATE INDEX idx_song_submitter ON song (submitter_id);
CREATE INDEX idx_song_country ON song (country_id);
CREATE INDEX idx_song_year ON song (year_id);

CREATE INDEX idx_vote_set_voter ON vote_set (voter_id);
CREATE INDEX idx_vote_set_show ON vote_set (show_id);

-- song_counts CTE in bias calculations
CREATE INDEX idx_song_show_song_show ON song_show (song_id, show_id);

-- song_counts and given_* CTEs in bias calculations
CREATE INDEX idx_song_id_country_id ON song (id, country_id);

-- ranked_points CTE in bias calculations
CREATE INDEX idx_point_system_id_score ON point (point_system_id, score DESC);

-- given_* CTEs in bias calculations
CREATE INDEX idx_vote_vote_set_song_point ON vote (vote_set_id, song_id, point_id);

COMMIT;