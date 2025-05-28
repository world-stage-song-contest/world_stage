BEGIN TRANSACTION;

PRAGMA legacy_alter_table = TRUE;

ALTER TABLE country ADD COLUMN pot INTEGER;

ALTER TABLE year RENAME TO year_old;

CREATE TABLE year (
    id INTEGER PRIMARY KEY,
    closed INTEGER,
    host TEXT,
    FOREIGN KEY (host) REFERENCES country (id)
);

INSERT INTO year (id, closed)
SELECT id, closed FROM year_old;

DROP TABLE year_old;

PRAGMA legacy_alter_table = FALSE;

COMMIT;