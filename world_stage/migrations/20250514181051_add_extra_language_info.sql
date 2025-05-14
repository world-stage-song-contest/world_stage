BEGIN TRANSACTION;
PRAGMA legacy_alter_table = TRUE;

ALTER TABLE song RENAME TO song_old;

CREATE TABLE song (
	id INTEGER,
	submitter_id INTEGER,
	country_id TEXT,
	year_id INTEGER,
	title TEXT,
	artist TEXT,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	modified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	native_title TEXT,
	translated_lyrics TEXT,
	romanized_lyrics TEXT,
	native_lyrics TEXT,
	video_link TEXT,
	snippet_start INTEGER,
	snippet_end INTEGER,
	is_placeholder INTEGER,
    title_language_id INTEGER,
    native_language_id INTEGER,
	UNIQUE(year_id,title),
	PRIMARY KEY(id),
	FOREIGN KEY(submitter_id) REFERENCES user(id),
	FOREIGN KEY(year_id) REFERENCES year(id),
	FOREIGN KEY(country_id) REFERENCES country(id)
);

INSERT INTO song (id, submitter_id, country_id, year_id, title, artist, created_at, modified_at, native_title, translated_lyrics, romanized_lyrics, native_lyrics, video_link, snippet_start, snippet_end, is_placeholder)
SELECT id, submitter_id, country_id, year_id, title, artist, created_at, modified_at, native_title, translated_lyrics, romanized_lyrics, native_lyrics, video_link, snippet_start, snippet_end, is_placeholder
FROM song_old;

ALTER TABLE language ADD COLUMN tag TEXT;
ALTER TABLE language ADD COLUMN extlang TEXT;
ALTER TABLE language ADD COLUMN region TEXT;
ALTER TABLE language ADD COLUMN subvariant TEXT;
ALTER TABLE language ADD COLUMN suppress_script TEXT;

DROP TABLE song_language;

CREATE TABLE song_language (
    id INTEGER PRIMARY KEY,
    song_id INTEGER,
    language_id INTEGER,
	priority INTEGER,
    FOREIGN KEY (song_id) REFERENCES song (id),
    FOREIGN KEY (language_id) REFERENCES language (id),
	UNIQUE(song_id, language_id),
	UNIQUE(song_id, priority)
);

PRAGMA legacy_alter_table = FALSE;

COMMIT;