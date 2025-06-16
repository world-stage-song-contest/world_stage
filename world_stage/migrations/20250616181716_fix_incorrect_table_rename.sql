BEGIN TRANSACTION;
PRAGMA legacy_alter_table = TRUE;

ALTER TABLE song_language RENAME TO song_language_old;
CREATE TABLE "song_language" (
	"id"	INTEGER,
	"song_id"	INTEGER,
	"language_id"	INTEGER,
	"priority"	INTEGER,
	UNIQUE("song_id","language_id"),
	PRIMARY KEY("id"),
	UNIQUE("song_id","priority"),
	FOREIGN KEY("song_id") REFERENCES "song"("id"),
	FOREIGN KEY("language_id") REFERENCES "language"("id") ON DELETE CASCADE
);

INSERT INTO song_language SELECT * FROM song_language_old;
DROP TABLE song_language_old;

ALTER TABLE song_show RENAME TO song_show_old;
CREATE TABLE "song_show" (
	"id"	INTEGER,
	"song_id"	INTEGER,
	"show_id"	INTEGER,
	"running_order"	INTEGER,
	PRIMARY KEY("id"),
	UNIQUE("song_id","show_id"),
	FOREIGN KEY("song_id") REFERENCES "song"("id"),
	FOREIGN KEY("show_id") REFERENCES "show"("id")
);
INSERT INTO song_show SELECT * FROM song_show_old;
DROP TABLE song_show_old;

ALTER TABLE vote RENAME TO vote_old;
CREATE TABLE "vote" (
	"id"	INTEGER,
	"vote_set_id"	INTEGER,
	"song_id"	INTEGER,
	"point_id"	INTEGER,
	PRIMARY KEY("id"),
	UNIQUE("vote_set_id","song_id"),
	UNIQUE("vote_set_id","point_id"),
	FOREIGN KEY("vote_set_id") REFERENCES "vote_set"("id") ON DELETE CASCADE,
	FOREIGN KEY("song_id") REFERENCES "song"("id"),
	FOREIGN KEY("point_id") REFERENCES "point"("id")
);
INSERT INTO vote SELECT * FROM vote_old;
DROP TABLE vote_old;

-- Remove any song_language entries that reference songs that no longer exist
DELETE FROM song_language WHERE song_id NOT IN (SELECT id FROM song);

PRAGMA legacy_alter_table = FALSE;
COMMIT;