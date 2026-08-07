ALTER TABLE year
ADD COLUMN IF NOT EXISTS scoreboard_style text;

UPDATE year
SET scoreboard_style = 'esc-1997'
WHERE id >= 1995
  AND scoreboard_style IS NULL;
