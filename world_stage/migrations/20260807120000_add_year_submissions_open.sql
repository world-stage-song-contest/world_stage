ALTER TABLE year
ADD COLUMN submissions_open boolean NOT NULL DEFAULT false;

UPDATE year
SET submissions_open = true
WHERE status = 'open';
