BEGIN TRANSACTION;

ALTER TABLE vote_set RENAME TO vote_set_old;
ALTER TABLE vote RENAME TO vote_old;
ALTER TABLE show RENAME TO show_old;
ALTER TABLE song RENAME TO song_old;
ALTER TABLE song_show RENAME TO song_show_old;
ALTER TABLE migration RENAME TO migration_old;

CREATE TABLE vote_set (
    id INTEGER PRIMARY KEY,
    voter_id INTEGER,
    show_id INTEGER,
    country_id TEXT,
    nickname TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (voter_id) REFERENCES user (id),
    FOREIGN KEY (show_id) REFERENCES show (id),
    FOREIGN KEY (country_id) REFERENCES country (id),
    UNIQUE(voter_id, show_id)
);

CREATE TABLE vote (
    id INTEGER PRIMARY KEY,
    vote_set_id INTEGER,
    song_id INTEGER,
    point_id INTEGER,
    FOREIGN KEY (vote_set_id) REFERENCES vote_set (id) ON DELETE CASCADE,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (point_id) REFERENCES point (id),
    UNIQUE(vote_set_id, song_id),
    UNIQUE(vote_set_id, point_id)
);

CREATE TABLE show (
    id INTEGER PRIMARY KEY,
    year_id INTEGER,
    point_system_id INTEGER,
    show_name INTEGER,
    short_name TEXT COLLATE NOCASE,
    voting_opens DATETIME,
    voting_closes DATETIME,
    date DATE,
    FOREIGN KEY (year_id) REFERENCES year (id),
    FOREIGN KEY (point_system_id) REFERENCES point_system (id),
    UNIQUE(year_id, show_name)
);

CREATE TABLE language (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE song (
    id INTEGER PRIMARY KEY,
    submitter_id INTEGER,
    country_id TEXT,
    year_id INTEGER,
    title TEXT,
    artist TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    modified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (submitter_id) REFERENCES user (id),
    FOREIGN KEY (country_id) REFERENCES country (id),
    FOREIGN KEY (year_id) REFERENCES year (id),
    UNIQUE(year_id, title)
);

CREATE TABLE song_language (
    id INTEGER PRIMARY KEY,
    song_id INTEGER,
    language_id INTEGER,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (language_id) REFERENCES language (id),
);

CREATE TABLE song_show (
    id INTEGER PRIMARY KEY,
    song_id INTEGER,
    show_id INTEGER,
    running_order INTEGER,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (show_id) REFERENCES show (id),
    UNIQUE(song_id, show_id)
);

CREATE TABLE migration (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name)
);

INSERT INTO vote_set (id, voter_id, show_id, country_id, nickname, created_at)
SELECT id, voter_id, show_id, country_id, nickname, created_at
FROM vote_set_old;

INSERT INTO vote (id, vote_set_id, song_id, point_id)
SELECT id, vote_set_id, song_id, point_id
FROM vote_old;

INSERT INTO show (id, year_id, point_system_id, show_name, short_name, voting_opens, voting_closes)
SELECT id, year_id, point_system_id, show_name, short_name, voting_opens, voting_closes
FROM show_old;

INSERT INTO song (id, submitter_id, country_id, year_id, title, artist, created_at)
SELECT id, submitter_id, country_id, year_id, title, artist, created_at
FROM song_old;

INSERT INTO song_show (id, song_id, show_id, running_order)
SELECT id, song_id, show_id, running_order
FROM song_show_old;

INSERT INTO migration (id, name, created_at)
SELECT id, name, created_at
FROM migration_old;

DROP TABLE vote_set_old;
DROP TABLE vote_old;
DROP TABLE show_old;
DROP TABLE song_old;
DROP TABLE song_show_old;
DROP TABLE migration_old;

COMMIT;