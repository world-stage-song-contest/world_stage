BEGIN;

ALTER TABLE song ADD COLUMN snippet2_start integer;
ALTER TABLE song ADD COLUMN snippet2_end integer;

ALTER TABLE song ADD CONSTRAINT song_snippet2_max_duration
    CHECK (
        snippet2_start IS NULL
        OR snippet2_end IS NULL
        OR snippet2_end - snippet2_start <= 10
    );

COMMIT;
