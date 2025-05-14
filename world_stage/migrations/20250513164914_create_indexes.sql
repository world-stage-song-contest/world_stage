BEGIN TRANSACTION;

-- Country indexes
CREATE INDEX country_alpha2_index ON country (cc2);

-- User indexes
CREATE INDEX user_username_index ON user (username);
CREATE INDEX user_email_index ON user (email);

-- Session indexes
CREATE INDEX session_id_index ON session (session_id);

COMMIT;