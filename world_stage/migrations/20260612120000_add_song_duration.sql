BEGIN;

-- Media duration in seconds, probed with ffprobe from the file on
-- media.world-stage.org. Only maintained for links on that host; NULL
-- for placeholders, external links, and files not yet probed. Used by
-- the radio to build a gapless continuous schedule.
ALTER TABLE song ADD COLUMN IF NOT EXISTS duration double precision;

COMMIT;
