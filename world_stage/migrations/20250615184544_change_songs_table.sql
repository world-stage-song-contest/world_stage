BEGIN TRANSACTION;

ALTER TABLE song RENAME TO song_old;

CREATE TABLE "song" (
	"id"	INTEGER,
	"submitter_id"	INTEGER,
	"country_id"	TEXT,
	"year_id"	INTEGER,
	"title"	TEXT,
	"artist"	TEXT,
	"created_at"	DATETIME DEFAULT CURRENT_TIMESTAMP,
	"modified_at"	DATETIME DEFAULT CURRENT_TIMESTAMP,
	"native_title"	TEXT,
	"translated_lyrics"	TEXT,
	"romanized_lyrics"	TEXT,
	"native_lyrics"	TEXT,
	"video_link"	TEXT,
	"snippet_start"	INTEGER,
	"snippet_end"	INTEGER,
	"is_placeholder"	INTEGER,
	"title_language_id"	INTEGER,
	"native_language_id"	INTEGER,
	"notes"	TEXT,
	"sources"	TEXT,
	"admin_approved"	INTEGER,
	PRIMARY KEY("id"),
	UNIQUE("year_id","country_id"),
	FOREIGN KEY("submitter_id") REFERENCES "user"("id"),
	FOREIGN KEY("year_id") REFERENCES "year"("id"),
	FOREIGN KEY("country_id") REFERENCES "country"("id")
);

INSERT INTO song SELECT * FROM song_old;

DROP TABLE song_old;
COMMIT;
