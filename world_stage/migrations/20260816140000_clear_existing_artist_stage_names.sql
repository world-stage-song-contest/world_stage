UPDATE artist_credit
SET stage_name = NULL
WHERE stage_name IS NOT NULL;
