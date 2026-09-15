BEGIN;

CREATE TABLE song_play (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    song_id bigint NOT NULL,
    played_at timestamptz NOT NULL,
    title text,
    artist text,
    media_url text,
    duration double precision,
    country_code text NOT NULL,
    country_name text NOT NULL,
    year_id bigint NOT NULL,
    year_label text NOT NULL,
    entry_number integer,
    special_short_name text
);

CREATE INDEX song_play_user_played_at_idx ON song_play (user_id, played_at DESC, id DESC);

COMMIT;
