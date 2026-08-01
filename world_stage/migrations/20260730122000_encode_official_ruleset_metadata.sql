-- Keep non-voter penalty behavior on the immutable ruleset version instead of
-- inferring it from a show's historical year.

ALTER TABLE voting_ruleset
    ADD COLUMN IF NOT EXISTS penalizes_non_voters boolean;

UPDATE voting_ruleset
SET penalizes_non_voters = version = 'v5'
WHERE penalizes_non_voters IS DISTINCT FROM (version = 'v5');

ALTER TABLE voting_ruleset
    ALTER COLUMN penalizes_non_voters SET NOT NULL;
