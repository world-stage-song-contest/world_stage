-- Support the global timestamp ordering used by the admin change-history feed.
CREATE INDEX IF NOT EXISTS idx_song_data_change_order
    ON song_data (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_song_status_change_order
    ON song_status (created_at DESC, id DESC);
