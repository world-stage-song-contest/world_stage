BEGIN;

CREATE TABLE year_spot_watch (
    account_id bigint NOT NULL
        REFERENCES account (id) ON UPDATE RESTRICT ON DELETE CASCADE,
    year_id bigint NOT NULL
        REFERENCES year (id) ON UPDATE RESTRICT ON DELETE CASCADE,
    country_id text NOT NULL
        REFERENCES country (id) ON UPDATE RESTRICT ON DELETE CASCADE,
    entry_number integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, year_id, country_id, entry_number)
);

CREATE INDEX year_spot_watch_spot_idx
    ON year_spot_watch (year_id, country_id, entry_number);

COMMIT;
