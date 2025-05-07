CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    UNIQUE(username)
)

CREATE TABLE IF NOT EXISTS country (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(name)
)

CREATE TABLE IF NOT EXISTS year (
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL
)

CREATE TABLE IF NOT EXISTS song (
    id INTEGER PRIMARY KEY,
    submitter_id INTEGER,
    country_id INTEGER,
    year_id INTEGER,
    title TEXT,
    artist TEXT,
    running_order INTEGER,
    FOREIGN KEY (submitter_id) REFERENCES user (id),
    FOREIGN KEY (country_id) REFERENCES country (id),
    FOREIGN KEY (year_id) REFERENCES year (id)
)

CREATE TABLE IF NOT EXISTS show (
    id INTEGER PRIMARY KEY,
    year_id INTEGER,
    show_name INTEGER,
    short_name TEXT,
    voting_opens TEXT,
    voting_closes TEXT,
    FOREIGN KEY (year_id) REFERENCES year (id),
    UNIQUE(year_id, show_name)
)

CREATE TABLE IF NOT EXISTS song_show (
    id INTEGER PRIMARY KEY,
    song_id INTEGER,
    show_id INTEGER,
    running_order INTEGER,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (show_id) REFERENCES show (id),
    UNIQUE(song_id, show_id)
)

CREATE TABLE IF NOT EXISTS vote_set (
    id INTEGER PRIMARY KEY,
    voter_id INTEGER,
    show_id INTEGER,
    country_id INTEGER,
    nickname TEXT,
    created_at TEXT,
    FOREIGN KEY (voter_id) REFERENCES voter (id),
    FOREIGN KEY (show_id) REFERENCES show (id),
    FOREIGN KEY (country_id) REFERENCES country (id)
)

CREATE TABLE IF NOT EXISTS vote (
    id INTEGER PRIMARY KEY,
    vote_set_id INTEGER,
    song_id INTEGER,
    points INTEGER,
    FOREIGN KEY (vote_set_id) REFERENCES vote_set (id) ON DELETE CASCADE,
    FOREIGN KEY (song_id) REFERENCES song (id)
)