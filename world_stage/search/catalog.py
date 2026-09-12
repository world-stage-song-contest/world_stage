from dataclasses import dataclass

# Use only the latest song data for names and lyrics.
ENTRY_CTE = """
WITH entries AS MATERIALIZED (
    SELECT s.id, s.country_id, s.year_id, s.entry_number, s.main_participant,
           d.title, d.native_title, d.artist_credit_set_id, d.submitter_id,
           d.language_set_id, d.genre_set_id, d.duration, d.video_link,
           d.native_lyrics, d.romanized_lyrics, d.translated_lyrics,
           y.status AS year_status, y.special_name, y.special_short_name,
           c.name AS country_name, c.cc3, a.username
    FROM song s
    JOIN year y ON y.id = s.year_id
    JOIN country c ON c.id = s.country_id
    JOIN LATERAL (
        SELECT data.* FROM song_data data
        WHERE data.song_id = s.id OR (
            data.song_id IS NULL AND data.country_id = s.country_id
            AND data.year_id = s.year_id
            AND data.entry_number IS NOT DISTINCT FROM s.entry_number
        )
        ORDER BY data.created_at DESC, data.id DESC LIMIT 1
    ) d ON true
    LEFT JOIN LATERAL (
        SELECT status.is_placeholder FROM song_status status
        WHERE status.song_id = s.id
        ORDER BY status.created_at DESC, status.id DESC LIMIT 1
    ) state ON true
    LEFT JOIN account a ON a.id = d.submitter_id
    WHERE d.title IS NOT NULL AND d.artist_credit_set_id IS NOT NULL
      AND NOT COALESCE(state.is_placeholder, false)
)
"""


@dataclass(frozen=True)
class Source:
    from_sql: str
    id: str
    name: str
    route: str
    fields: dict[str, str]
    text_fields: tuple[tuple[str, int], ...]


ENTRY_FIELDS = {
    "title": "e.title",
    "native_title": "e.native_title",
    "artist": """ARRAY[artist_credit_name(e.artist_credit_set_id)] || ARRAY(
        SELECT DISTINCT name FROM artist_credit ac JOIN artist a ON a.id = ac.artist_id
        CROSS JOIN LATERAL unnest(ARRAY[a.full_name, a.native_name, ac.stage_name]) name
        WHERE ac.artist_credit_set_id = e.artist_credit_set_id AND name IS NOT NULL
    )""",
    "country": """ARRAY[e.country_name, e.country_id, e.cc3] || ARRAY(
        SELECT name FROM alternative_name an WHERE an.country_id = e.country_id
        AND (an.from_year_id IS NULL OR e.year_id >= an.from_year_id)
        AND (an.to_year_id IS NULL OR e.year_id <= an.to_year_id)
    )""",
    "submitter": "e.username",
    "year": "e.year_id",
    "special": "(e.year_id < 0)",
    "language": """ARRAY(
        SELECT l.name FROM language_set_language member
        JOIN language l ON l.id = member.language_id
        WHERE member.language_set_id = e.language_set_id
    )""",
    "genre": """ARRAY(
        SELECT g.name FROM genre_set_subgenre member
        JOIN subgenre g ON g.id = member.subgenre_id
        WHERE member.genre_set_id = e.genre_set_id
    )""",
    "lyrics": "array_remove(ARRAY[e.native_lyrics, e.romanized_lyrics, e.translated_lyrics], NULL)",
    "native_lyrics": "e.native_lyrics",
    "romanized_lyrics": "e.romanized_lyrics",
    "translated_lyrics": "e.translated_lyrics",
    "duration": "e.duration",
    "place": """(SELECT min(r.place) FROM country_year_results r
        WHERE r.song_id = e.id AND e.year_status = 'closed')""",
    "main_participant": "e.main_participant",
    "has_video": "(COALESCE(e.video_link, '') NOT IN ('', 'N/A'))",
}

ARTIST_NAME = (
    "CASE WHEN a.number = 1 THEN a.full_name ELSE a.full_name || ' (' || a.number || ')' END"
)
ARTIST_STAGES = """ARRAY(
    SELECT DISTINCT ac.stage_name FROM artist_credit ac
    WHERE ac.artist_id = a.id AND ac.stage_name IS NOT NULL
      AND EXISTS (SELECT 1 FROM entries e WHERE e.artist_credit_set_id = ac.artist_credit_set_id)
)"""
YEAR_NAME = "COALESCE(y.special_name, y.id::text)"
SHOW_NAME = "COALESCE(y.special_name, y.id::text) || ' ' || s.show_name"
COUNTRY_ALIASES = "ARRAY(SELECT name FROM alternative_name an WHERE an.country_id = c.id)"

SOURCES = {
    "entry": Source(
        "entries e",
        "e.id::text",
        "e.title",
        "jsonb_build_object('country', e.country_id, 'countryName', e.country_name, "
        "'year', e.year_id, 'special', e.special_short_name, 'entryNumber', e.entry_number)",
        ENTRY_FIELDS,
        (
            ("title", 10),
            ("native_title", 10),
            ("artist", 8),
            ("country", 5),
            ("year", 4),
            ("event_name", 4),
            ("language", 3),
            ("genre", 3),
            ("submitter", 2),
            ("lyrics", 1),
        ),
    ),
    "year": Source(
        "year y LEFT JOIN country c ON c.id = y.host_id",
        "y.id::text",
        YEAR_NAME,
        "jsonb_build_object('year', y.id, 'special', y.special_short_name)",
        {
            "year": "y.id",
            "special": "(y.id < 0)",
            "status": "y.status",
            "country": "array_remove(ARRAY[c.name, c.id, c.cc3], NULL)",
        },
        (("name", 10), ("short_name", 8), ("country", 4)),
    ),
    "country": Source(
        "country c",
        "c.id",
        "c.name",
        "jsonb_build_object('country', c.id, 'countryName', c.name)",
        {
            "code": "c.id",
            "alternative_name": COUNTRY_ALIASES,
            "country": "ARRAY[c.name, c.id, c.cc3] || " + COUNTRY_ALIASES,
        },
        (("country", 10),),
    ),
    "submitter": Source(
        "account a",
        "a.id::text",
        "a.username",
        "jsonb_build_object('username', a.username)",
        {"submitter": "a.username"},
        (("name", 10),),
    ),
    "artist": Source(
        "artist a",
        "a.id::text",
        ARTIST_NAME,
        f"jsonb_build_object('name', {ARTIST_NAME})",
        {
            "native_name": "a.native_name",
            "stage_name": ARTIST_STAGES,
            "artist": "array_remove(ARRAY[a.full_name, a.native_name], NULL) || " + ARTIST_STAGES,
        },
        (("name", 10), ("native_name", 10), ("stage_name", 8)),
    ),
    "show": Source(
        "show s JOIN year y ON y.id = s.year_id",
        "s.id::text",
        SHOW_NAME,
        "jsonb_build_object('year', y.id, 'special', y.special_short_name, 'show', s.short_name)",
        {
            "year": "y.id",
            "special": "(y.id < 0)",
            "status": "s.status",
            "show_type": "s.show_type",
            "date": "s.date::date",
            "time": "s.date::timestamp::time",
            "starts_at": "s.date::timestamptz",
            "voting_opens": "s.voting_opens",
            "voting_closes": "s.voting_closes",
        },
        (("name", 10), ("short_name", 6), ("date", 4)),
    ),
}

VISIBILITY = {
    "entry": "true",
    "year": "true",
    "country": "EXISTS (SELECT 1 FROM entries e WHERE e.country_id = c.id AND e.main_participant)",
    "submitter": "EXISTS (SELECT 1 FROM entries e WHERE e.submitter_id = a.id)",
    "artist": """EXISTS (SELECT 1 FROM entries e JOIN artist_credit ac
        ON ac.artist_credit_set_id = e.artist_credit_set_id WHERE ac.artist_id = a.id)""",
    "show": """s.national_final_id IS NULL AND s.status IN ('draw', 'partial', 'full')
        AND EXISTS (SELECT 1 FROM song_show ss JOIN entries e ON e.id = ss.song_id
                    WHERE ss.show_id = s.id)""",
}
