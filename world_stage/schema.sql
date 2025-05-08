CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    UNIQUE(username)
);

CREATE TABLE IF NOT EXISTS country (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_participating INTEGER,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS year (
    id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS alternative_name (
    id INTEGER PRIMARY KEY,
    country_id TEXT NOT NULL,
    from_year_id INTEGER NOT NULL,
    to_year_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    FOREIGN KEY (country_id) REFERENCES country (id),
    FOREIGN KEY (from_year_id) REFERENCES year (id),
    FOREIGN KEY (to_year_id) REFERENCES year (id)
);

CREATE TABLE IF NOT EXISTS point_system (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL,
    UNIQUE(number)
);

CREATE TABLE IF NOT EXISTS point (
    id INTEGER PRIMARY KEY,
    point_system_id INTEGER NOT NULL,
    place INTEGER NOT NULL,
    score INTEGER NOT NULL,
    FOREIGN KEY (point_system_id) REFERENCES point_system (id)
);

CREATE TABLE IF NOT EXISTS song (
    id INTEGER PRIMARY KEY,
    submitter_id INTEGER,
    country_id TEXT,
    year_id INTEGER,
    title TEXT,
    artist TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (submitter_id) REFERENCES user (id),
    FOREIGN KEY (country_id) REFERENCES country (id),
    FOREIGN KEY (year_id) REFERENCES year (id)
);

CREATE TABLE IF NOT EXISTS show (
    id INTEGER PRIMARY KEY,
    year_id INTEGER,
    point_system_id INTEGER,
    show_name INTEGER,
    short_name TEXT COLLATE NOCASE,
    voting_opens TEXT,
    voting_closes TEXT,
    FOREIGN KEY (year_id) REFERENCES year (id),
    FOREIGN KEY (point_system_id) REFERENCES point_system (id),
    UNIQUE(year_id, show_name)
);

CREATE TABLE IF NOT EXISTS song_show (
    id INTEGER PRIMARY KEY,
    song_id INTEGER,
    show_id INTEGER,
    running_order INTEGER,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (show_id) REFERENCES show (id),
    UNIQUE(song_id, show_id)
);

CREATE TABLE IF NOT EXISTS vote_set (
    id INTEGER PRIMARY KEY,
    voter_id INTEGER,
    show_id INTEGER,
    country_id TEXT,
    nickname TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (voter_id) REFERENCES voter (id),
    FOREIGN KEY (show_id) REFERENCES show (id),
    FOREIGN KEY (country_id) REFERENCES country (id)
);

CREATE TABLE IF NOT EXISTS vote (
    id INTEGER PRIMARY KEY,
    vote_set_id INTEGER,
    song_id INTEGER,
    point_id INTEGER,
    FOREIGN KEY (vote_set_id) REFERENCES vote_set (id) ON DELETE CASCADE,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (point_id) REFERENCES point (id)
);

CREATE TABLE IF NOT EXISTS migration (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name)
);