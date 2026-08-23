INSERT INTO song_approval_status (name)
VALUES ('pending-second-opinion')
ON CONFLICT DO NOTHING;
