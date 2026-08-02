CREATE TABLE song_revision_merge (
    song_data_id bigint PRIMARY KEY
        REFERENCES song_data (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    merged_into_song_data_id bigint NOT NULL
        REFERENCES song_data (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    merged_by bigint
        REFERENCES account (id) ON UPDATE RESTRICT ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT song_revision_merge_different_revision
        CHECK (song_data_id <> merged_into_song_data_id)
);

CREATE TABLE song_verification_hidden_revision (
    song_data_id bigint PRIMARY KEY
        REFERENCES song_data (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    hidden_by bigint
        REFERENCES account (id) ON UPDATE RESTRICT ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
