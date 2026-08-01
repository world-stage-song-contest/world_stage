-- Store the midpoint-based points percentage alongside the existing metric.
-- These columns remain nullable until the voting rules and result calculation
-- can populate them for every official and revote result.

ALTER TABLE country_show_results
    ADD COLUMN IF NOT EXISTS adjusted_points_percentage numeric(5,2),
    ADD COLUMN IF NOT EXISTS adjusted_max_possible_points integer,
    ADD COLUMN IF NOT EXISTS points_midpoint numeric(12,6);
