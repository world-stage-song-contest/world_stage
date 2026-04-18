BEGIN;

-- Allow alternative_name to override the flag asset for a country during a
-- given year range — e.g. West Germany 1960-1989. The variant slots into the
-- /files/flags/<cc>/<variant>/{rect,square}.svg path the route already
-- understands once the v= query param is honoured.
ALTER TABLE alternative_name ADD COLUMN flag_variant text;

COMMIT;
