ALTER TABLE account
    ADD COLUMN settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT account_settings_object_check
        CHECK (jsonb_typeof(settings) = 'object');

CREATE TABLE show_email_notification_delivery (
    account_id bigint NOT NULL
        REFERENCES account (id) ON UPDATE RESTRICT ON DELETE CASCADE,
    show_id bigint NOT NULL
        REFERENCES show (id) ON UPDATE RESTRICT ON DELETE CASCADE,
    sent_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, show_id)
);
