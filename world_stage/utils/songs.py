from dataclasses import dataclass, field
from functools import lru_cache, total_ordering
from typing import LiteralString, Self

from ..db import get_db
from .lookups import get_show_id
from .timefmt import format_seconds
from .types import Country, Language, VoteData, Year


@total_ordering
@dataclass
class Song:
    id: int
    title: str
    artist: str
    artists: list[dict]
    country: Country
    year: Year
    entry_number: int
    placeholder: bool
    approval_status: str
    languages: list[Language]
    vote_data: VoteData | None
    submitter: str | None
    submitter_id: int | None
    native_title: str | None
    title_lang: Language
    native_lang: Language
    translated_lyrics: str | None
    latin_lyrics: str | None
    native_lyrics: str | None
    lyrics_notes: str | None
    video_link: str | None
    poster_link: str | None
    vtt_link: str | None
    duration: float | None
    sources: str | None
    recap_start_seconds: int | None = None
    _recap_end_seconds: int | None = None
    recap2_start_seconds: int | None = None
    _recap2_end_seconds: int | None = None
    key_signatures: list[str] = field(default_factory=list)
    key_signature_timeline: list[dict] = field(default_factory=list)
    time_signatures: list[str] = field(default_factory=list)
    time_signature_timeline: list[dict] = field(default_factory=list)
    subgenres: list[str] = field(default_factory=list)
    hidden: bool = False

    @property
    def recap_end_seconds(self) -> int | None:
        if self._recap_end_seconds is not None:
            return self._recap_end_seconds
        if self.recap_start_seconds is not None:
            return self.recap_start_seconds + 20
        return None

    @property
    def recap2_end_seconds(self) -> int | None:
        if self._recap2_end_seconds is not None:
            return self._recap2_end_seconds
        if self.recap2_start_seconds is not None:
            return self.recap2_start_seconds + 10
        return None

    @property
    def recap_start(self) -> str | None:
        if self.recap_start_seconds is None:
            return None
        return format_seconds(self.recap_start_seconds)

    @property
    def recap_end(self) -> str | None:
        if self.recap_end_seconds is None:
            return None
        return format_seconds(self.recap_end_seconds)

    @property
    def recap2_start(self) -> str | None:
        if self.recap2_start_seconds is None:
            return None
        return format_seconds(self.recap2_start_seconds)

    @property
    def recap2_end(self) -> str | None:
        if self.recap2_end_seconds is None:
            return None
        return format_seconds(self.recap2_end_seconds)

    @classmethod
    def from_row(cls, song: dict) -> Self:
        """Build a Song from an already-hydrated query row without database I/O."""
        recap_start_seconds = song.get("snippet_start")
        recap_end_seconds = song.get("snippet_end")
        recap2_start_seconds = song.get("snippet2_start")
        recap2_end_seconds = song.get("snippet2_end")
        year = Year(
            id=song["year_id"],
            special_name=song.get("special_name"),
            special_short_name=song.get("special_short_name"),
            status=song.get("year_status"),
        )
        return cls(
            id=song["id"],
            title=song["title"],
            native_title=song.get("native_title"),
            artist=song["artist"],
            artists=song.get("artists") or [],
            video_link=song.get("video_link"),
            poster_link=song.get("poster_link"),
            vtt_link=song.get("vtt_link"),
            duration=song.get("duration"),
            country=Country(
                cc=song["country_id"],
                name=song["name"],
                is_participating=bool(song["is_participating"]),
                cc3=song["cc3"],
                flag_variant=song.get("flag_variant"),
            ),
            placeholder=bool(song.get("is_placeholder", False)),
            approval_status=song.get("approval_status", "pending"),
            year=year,
            entry_number=song.get("entry_number") or 1,
            title_lang=_language_from_row(song, "title_language"),
            submitter_id=song.get("submitter_id"),
            native_lang=_language_from_row(song, "native_language"),
            translated_lyrics=song.get("translated_lyrics"),
            latin_lyrics=song.get("romanized_lyrics"),
            native_lyrics=song.get("native_lyrics"),
            lyrics_notes=song.get("notes"),
            sources=song.get("sources"),
            submitter=song.get("username"),
            languages=[],
            vote_data=_vote_data_from_row(song),
            recap_start_seconds=recap_start_seconds,
            _recap_end_seconds=recap_end_seconds,
            recap2_start_seconds=recap2_start_seconds,
            _recap2_end_seconds=recap2_end_seconds,
        )

    @property
    def duration_display(self) -> str:
        return format_seconds(round(self.duration)) if self.duration else ""

    def __lt__(self, other):
        if not isinstance(other, Song):
            return NotImplemented
        if self.vote_data is None or other.vote_data is None:
            return self.id < other.id
        else:
            return self.vote_data < other.vote_data

    def __eq__(self, other):
        if not isinstance(other, Song):
            return NotImplemented
        return self.id == other.id

    def as_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "country": self.country,
            "year": self.year.id,
            "placeholder": self.placeholder,
            "languages": [lang.as_dict() for lang in self.languages],
            "submitter": self.submitter,
            "native_title": self.native_title,
            "title_lang": self.title_lang.as_dict() if self.title_lang else None,
            "native_lang": self.native_lang.as_dict() if self.native_lang else None,
            "vote_data": self.vote_data.as_dict() if self.vote_data else None,
        }

    def get_pt(self, points: int) -> int | None:
        if self.vote_data is None:
            return None
        return self.vote_data.get_pt(points)


def _vote_data_from_row(song: dict) -> VoteData | None:
    """Hydrate cached result data when the song query selected it."""
    running_order = song.get("running_order")
    total_points = song.get("result_total_points")
    if total_points is None:
        return VoteData(running_order, None, None, None) if running_order is not None else None

    vote_data = VoteData(
        ro=running_order if running_order is not None else 0,
        total_votes=song.get("result_total_votes"),
        max_pts=song.get("result_max_pts"),
        show_voters=song.get("result_total_voters"),
        max_possible_points=song.get("result_max_possible_points"),
        points_percentage=song.get("result_points_percentage"),
        adjusted_max_possible_points=song.get("result_adjusted_max_possible_points"),
        points_midpoint=song.get("result_points_midpoint"),
        adjusted_points_percentage=song.get("result_adjusted_points_percentage"),
    )
    vote_data.sum = total_points
    vote_data.count = song.get("result_total_votes") or 0
    vote_data.penalty = song.get("result_penalty") or 0
    for pt_str, count in (song.get("result_point_distribution") or {}).items():
        vote_data.pts[int(pt_str)] = count
    return vote_data


def _language_from_row(row: dict, prefix: str) -> Language:
    if row.get(f"{prefix}_id") is None:
        return Language()
    return Language(
        name=row[f"{prefix}_name"],
        tag=row[f"{prefix}_tag"],
        extlang=row[f"{prefix}_extlang"],
        region=row[f"{prefix}_region"],
        subvariant=row[f"{prefix}_subvariant"],
        suppress_script=row[f"{prefix}_suppress_script"],
    )


def get_votes_for_song(
    song_id: int, show_id: int, ro: int, *, result_mode: str = "official"
) -> VoteData:
    return get_votes_for_songs({song_id: ro}, show_id, result_mode=result_mode)[song_id]


def get_votes_for_songs(
    running_orders: dict[int, int], show_id: int, *, result_mode: str = "official"
) -> dict[int, VoteData]:
    """Batch-load vote totals for every requested song in a show."""
    if not running_orders:
        return {}

    cursor = get_db().cursor()
    song_ids = list(running_orders)
    cursor.execute(
        """
        SELECT csr.song_id, csr.total_points, csr.total_votes_received,
               csr.point_distribution,
               csr.max_pts, csr.total_voters, csr.max_possible_points,
               csr.points_percentage, csr.adjusted_max_possible_points,
               csr.points_midpoint, csr.adjusted_points_percentage,
               COALESCE(
                   CASE WHEN csr.result_mode = 'revote' THEN ss.revote_penalty ELSE ss.penalty END,
                   0
               ) AS penalty
        FROM country_show_results csr
        LEFT JOIN song_show ss ON ss.song_id = csr.song_id AND ss.show_id = csr.show_id
        WHERE csr.song_id = ANY(%s) AND csr.show_id = %s
          AND csr.result_mode = %s
    """,
        (song_ids, show_id, result_mode),
    )

    result: dict[int, VoteData] = {}
    for row in cursor.fetchall():
        vote_data = VoteData(
            ro=running_orders[row["song_id"]],
            total_votes=row["total_votes_received"],
            max_pts=row["max_pts"],
            show_voters=row["total_voters"],
            max_possible_points=row["max_possible_points"],
            points_percentage=row["points_percentage"],
            adjusted_max_possible_points=row["adjusted_max_possible_points"],
            points_midpoint=row["points_midpoint"],
            adjusted_points_percentage=row["adjusted_points_percentage"],
        )
        vote_data.sum = row["total_points"]
        vote_data.count = row["total_votes_received"]
        vote_data.penalty = row["penalty"] or 0
        for pt_str, cnt in (row["point_distribution"] or {}).items():
            vote_data.pts[int(pt_str)] = cnt
        result[row["song_id"]] = vote_data

    missing_ids = sorted(song_id for song_id in song_ids if song_id not in result)
    if missing_ids:
        raise RuntimeError(f"Missing {result_mode} result rows for show {show_id}: {missing_ids}")
    return result


@lru_cache(maxsize=512)
def get_language(lang_id: int) -> Language | None:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT name, tag, extlang, region, subvariant, suppress_script FROM language
        WHERE id = %s
    """,
        (lang_id,),
    )
    lang = cursor.fetchone()
    if not lang:
        return None

    return Language(**lang)


_KEY_SIGNATURE_TONIC_ORDER = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
_KEY_SIGNATURE_TONIC_INDEX = {t: i for i, t in enumerate(_KEY_SIGNATURE_TONIC_ORDER)}


def _display_tonic(tonic: str | None) -> str:
    """Render a tonic with proper Unicode accidentals (♭, ♯) when it's
    one of the canonical 12 spellings. Free-form "Other" tonics are
    returned unchanged so user-supplied formatting is preserved."""
    if tonic is None:
        return ""
    if tonic in _KEY_SIGNATURE_TONIC_INDEX:
        return tonic.replace("b", "♭").replace("#", "♯")
    return tonic


def get_song_key_signatures(song_id: int) -> list[str]:
    """Return human-readable key signature labels for a song.

    Sorted by canonical tonic order (C → B), then alphabetically by
    mode. Atonal rows (tonic AND mode both NULL) are excluded. Free-form
    "Other" tonics that don't match the canonical 12-tone set sort after
    the canonical tonics. Microtonal rows are annotated with a
    " (microtonal)" suffix; if the same (tonic, mode) is recorded both
    with and without the flag the annotation wins.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT member.tonic, member.mode, member.microtonal, member.notes
        FROM song
        JOIN LATERAL (
            SELECT song_data.key_signature_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN key_signature_set_key_signature AS member
          ON member.key_signature_set_id = data.key_signature_set_id
        WHERE song.id = %s
          AND (tonic IS NOT NULL OR mode IS NOT NULL)
    """,
        (song_id,),
    )
    seen: dict[tuple[str | None, str | None], dict[str, bool]] = {}
    for r in cursor.fetchall():
        key = (r["tonic"], r["mode"])
        flags = seen.setdefault(key, {"microtonal": False, "has_notes": False})
        flags["microtonal"] = flags["microtonal"] or bool(r["microtonal"])
        flags["has_notes"] = flags["has_notes"] or bool(r["notes"])

    def sort_key(item):
        tonic, mode = item[0]
        idx = _KEY_SIGNATURE_TONIC_INDEX.get(tonic, len(_KEY_SIGNATURE_TONIC_ORDER))
        return (idx, (tonic or "").lower(), (mode or "").lower())

    out: list[str] = []
    for (tonic, mode), flags in sorted(seen.items(), key=sort_key):
        label = " ".join(p for p in (_display_tonic(tonic), mode) if p)
        if flags["microtonal"]:
            label += " (microtonal)"
        if flags["has_notes"]:
            label += "*"
        out.append(label)
    return out


def get_song_time_signatures(song_id: int) -> list[str]:
    """Return human-readable time signature labels for a song.

    Deduped by (numerator, denominator) and ordered by first appearance
    in the song. Numerator and denominator are joined with a fraction
    slash (U+2044) — e.g. ``4⁄4``. Mixed-meter sections (both NULL)
    render as ``mixed meter``.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT member.numerator, member.denominator
        FROM song
        JOIN LATERAL (
            SELECT song_data.time_signature_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN time_signature_set_time_signature AS member
          ON member.time_signature_set_id = data.time_signature_set_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    seen: set[tuple[int | None, int | None]] = set()
    out: list[str] = []
    for r in cursor.fetchall():
        key = (r["numerator"], r["denominator"])
        if key in seen:
            continue
        seen.add(key)
        if key == (None, None):
            out.append("mixed meter")
        else:
            out.append(f"{r['numerator']}⁄{r['denominator']}")
    return out


def get_song_subgenres_display(song_id: int) -> list[str]:
    """Return subgenre names for a song in user-selected priority order."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT subgenre.name
        FROM song
        JOIN LATERAL (
            SELECT song_data.genre_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN genre_set_subgenre AS member
          ON member.genre_set_id = data.genre_set_id
        JOIN subgenre ON subgenre.id = member.subgenre_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    return [r["name"] for r in cursor.fetchall()]


def get_song_time_signature_timeline(song_id: int) -> list[dict]:
    """Return time signatures in chronological order, formatted for the
    click-to-seek timeline below the video. Mixed-meter sections are
    included (rendered as ``mixed meter``) so the timeline reflects the
    actual structure of the song.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT member.start_seconds, member.numerator, member.denominator,
               member.notes
        FROM song
        JOIN LATERAL (
            SELECT song_data.time_signature_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN time_signature_set_time_signature AS member
          ON member.time_signature_set_id = data.time_signature_set_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    rows: list[dict] = []
    for r in cursor.fetchall():
        num, den = r["numerator"], r["denominator"]
        label = "mixed meter" if num is None and den is None else f"{num}⁄{den}"
        rows.append(
            {
                "start_seconds": r["start_seconds"],
                "start_label": format_seconds(r["start_seconds"]) or "0:00",
                "label": label,
                "notes": r["notes"],
            }
        )
    return rows


def get_song_key_signature_timeline(song_id: int) -> list[dict]:
    """Return key signatures in chronological order, formatted for the
    click-to-seek timeline below the video. Atonal sections are
    included (rendered as ``atonal``) so the timeline reflects the
    actual structure of the song.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT member.start_seconds, member.tonic, member.mode,
               member.microtonal, member.notes
        FROM song
        JOIN LATERAL (
            SELECT song_data.key_signature_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN key_signature_set_key_signature AS member
          ON member.key_signature_set_id = data.key_signature_set_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    rows: list[dict] = []
    for r in cursor.fetchall():
        tonic, mode = r["tonic"], r["mode"]
        microtonal = bool(r["microtonal"])
        if tonic is None and mode is None:
            label = "atonal"
        else:
            label = " ".join(p for p in (_display_tonic(tonic), mode) if p)
        if microtonal:
            label += " (microtonal)"
        rows.append(
            {
                "start_seconds": r["start_seconds"],
                "start_label": format_seconds(r["start_seconds"]) or "0:00",
                "label": label,
                "notes": r["notes"],
            }
        )
    return rows


def get_song_languages(song_id: int) -> list[Language]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT language.name, language.tag, language.extlang, language.region, language.subvariant,
               language.suppress_script
        FROM song
        JOIN LATERAL (
            SELECT song_data.language_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN language_set_language AS member
          ON member.language_set_id = data.language_set_id
        JOIN language ON member.language_id = language.id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    languages = [Language(**lang) for lang in cursor.fetchall()]

    return languages


def get_languages_for_songs(song_ids: list[int]) -> dict[int, list[Language]]:
    """Batch-load languages for many songs in a single query."""
    if not song_ids:
        return {}

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id AS song_id,
               language.name, language.tag, language.extlang,
               language.region, language.subvariant, language.suppress_script
        FROM song
        JOIN LATERAL (
            SELECT song_data.language_set_id
            FROM song_data
            WHERE song_data.song_id = song.id
               OR (
                   song_data.song_id IS NULL
                   AND song_data.country_id = song.country_id
                   AND song_data.year_id = song.year_id
                   AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
               )
            ORDER BY song_data.created_at DESC, song_data.id DESC
            LIMIT 1
        ) AS data ON true
        JOIN language_set_language AS member
          ON member.language_set_id = data.language_set_id
        JOIN language ON member.language_id = language.id
        WHERE song.id = ANY(%s)
        ORDER BY song.id, member.priority
    """,
        (song_ids,),
    )

    result: dict[int, list[Language]] = {sid: [] for sid in song_ids}
    for row in cursor.fetchall():
        sid = row.pop("song_id")
        result[sid].append(Language(**row))
    return result


# Shared skeleton for the song fetchers below. Fragments are typed
# LiteralString so pyright rejects any runtime string reaching
# cursor.execute; bind values always go through query parameters.
_SONG_COLUMNS: LiteralString = """
    song.id, data.title,
    artist_credit_name(data.artist_credit_set_id) AS artist,
    COALESCE((
        SELECT JSONB_AGG(JSONB_BUILD_OBJECT(
            'full_name', artist.full_name,
            'display_name', CASE WHEN artist.number = 1 THEN artist.full_name
                ELSE artist.full_name || ' (' || artist.number || ')' END,
            'stage_name', credit.stage_name,
            'join', credit.join_phrase
        ) ORDER BY credit.position)
        FROM artist_credit AS credit
        JOIN artist ON artist.id = credit.artist_id
        WHERE credit.artist_credit_set_id = data.artist_credit_set_id
    ), '[]'::jsonb) AS artists,
    data.native_title,
    song.country_id, COALESCE(an.name, country.name) AS name,
    country.is_participating, country.cc3, an.flag_variant,
    COALESCE(status.is_placeholder, false) AS is_placeholder,
    COALESCE(status.approval_status, 'pending') AS approval_status,
    data.native_language_id, data.title_language_id,
    data.native_lyrics, data.romanized_lyrics, data.translated_lyrics,
    account.username, song.year_id, data.poster_link, data.vtt_link,
    data.video_link, data.duration, data.snippet_start, data.snippet_end,
    data.snippet2_start, data.snippet2_end,
    data.submitter_id, data.notes, data.sources, song.entry_number,
    year.special_name, year.special_short_name, year.status AS year_status,
    title_language.name AS title_language_name,
    title_language.tag AS title_language_tag,
    title_language.extlang AS title_language_extlang,
    title_language.region AS title_language_region,
    title_language.subvariant AS title_language_subvariant,
    title_language.suppress_script AS title_language_suppress_script,
    native_language.name AS native_language_name,
    native_language.tag AS native_language_tag,
    native_language.extlang AS native_language_extlang,
    native_language.region AS native_language_region,
    native_language.subvariant AS native_language_subvariant,
    native_language.suppress_script AS native_language_suppress_script"""

_SONG_JOINS: LiteralString = """
FROM song
JOIN LATERAL (
    SELECT song_data.*
    FROM song_data
    WHERE song_data.song_id = song.id
       OR (
           song_data.song_id IS NULL
           AND song_data.country_id = song.country_id
           AND song_data.year_id = song.year_id
           AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
       )
    ORDER BY song_data.created_at DESC, song_data.id DESC
    LIMIT 1
) AS data ON true
LEFT JOIN LATERAL (
    SELECT song_status.approval_status, song_status.is_placeholder
    FROM song_status
    WHERE song_status.song_id = song.id
    ORDER BY song_status.created_at DESC, song_status.id DESC
    LIMIT 1
) AS status ON true
JOIN country ON song.country_id = country.id
LEFT JOIN year ON year.id = song.year_id
LEFT OUTER JOIN account ON data.submitter_id = account.id
LEFT JOIN language title_language ON title_language.id = data.title_language_id
LEFT JOIN language native_language ON native_language.id = data.native_language_id
LEFT JOIN alternative_name an ON an.country_id = song.country_id
    AND (an.from_year_id IS NULL OR song.year_id >= an.from_year_id)
    AND (an.to_year_id IS NULL OR song.year_id <= an.to_year_id)"""

_CYR_JOIN: LiteralString = """
LEFT JOIN country_year_results cyr ON cyr.song_id = song.id"""

_YEAR_PLACE_ORDER: LiteralString = """
    CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
    country.name"""


def _songs_from_rows(rows: list[dict]) -> list[Song]:
    """Map already-fetched rows without performing database work."""
    return [Song.from_row(row) for row in rows]


def _attach_languages(songs: list[Song]) -> None:
    languages_by_song = get_languages_for_songs([song.id for song in songs])
    for song in songs:
        song.languages = languages_by_song.get(song.id, [])


def _attach_show_results(songs: list[Song], show_id: int, result_mode: str) -> None:
    if songs:
        running_orders = {
            song.id: song.vote_data.ro for song in songs if song.vote_data is not None
        }
        votes_by_song = get_votes_for_songs(running_orders, show_id, result_mode=result_mode)
        for song in songs:
            song.vote_data = votes_by_song.get(song.id)


def _enrich_entry_details(song: Song) -> Song:
    """Load all metadata needed by the entry detail page in one query."""
    cursor = get_db().cursor()
    cursor.execute(
        """
        WITH data AS MATERIALIZED (
            SELECT song_data.*
            FROM song
            JOIN LATERAL (
                SELECT revision.*
                FROM song_data AS revision
                WHERE revision.song_id = song.id
                   OR (
                       revision.song_id IS NULL
                       AND revision.country_id = song.country_id
                       AND revision.year_id = song.year_id
                       AND revision.entry_number IS NOT DISTINCT FROM song.entry_number
                   )
                ORDER BY revision.created_at DESC, revision.id DESC
                LIMIT 1
            ) AS song_data ON true
            WHERE song.id = %s
        )
        SELECT
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'name', language.name,
                    'tag', language.tag,
                    'extlang', language.extlang,
                    'region', language.region,
                    'subvariant', language.subvariant,
                    'suppress_script', language.suppress_script
                ) ORDER BY member.priority)
                FROM language_set_language AS member
                JOIN language ON language.id = member.language_id
                WHERE member.language_set_id = data.language_set_id
            ), '[]'::jsonb) AS languages,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'start_seconds', member.start_seconds,
                    'tonic', member.tonic,
                    'mode', member.mode,
                    'microtonal', member.microtonal,
                    'notes', member.notes
                ) ORDER BY member.priority)
                FROM key_signature_set_key_signature AS member
                WHERE member.key_signature_set_id = data.key_signature_set_id
            ), '[]'::jsonb) AS key_signatures,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'start_seconds', member.start_seconds,
                    'numerator', member.numerator,
                    'denominator', member.denominator,
                    'notes', member.notes
                ) ORDER BY member.priority)
                FROM time_signature_set_time_signature AS member
                WHERE member.time_signature_set_id = data.time_signature_set_id
            ), '[]'::jsonb) AS time_signatures,
            COALESCE((
                SELECT jsonb_agg(subgenre.name ORDER BY member.priority)
                FROM genre_set_subgenre AS member
                JOIN subgenre ON subgenre.id = member.subgenre_id
                WHERE member.genre_set_id = data.genre_set_id
            ), '[]'::jsonb) AS subgenres
        FROM data
        """,
        (song.id,),
    )
    row = cursor.fetchone()
    if not row:
        return song

    song.languages = [Language(**language) for language in row["languages"]]

    key_rows = row["key_signatures"]
    seen_keys: dict[tuple[str | None, str | None], dict[str, bool]] = {}
    for key in key_rows:
        identity = (key["tonic"], key["mode"])
        flags = seen_keys.setdefault(identity, {"microtonal": False, "has_notes": False})
        flags["microtonal"] = flags["microtonal"] or bool(key["microtonal"])
        flags["has_notes"] = flags["has_notes"] or bool(key["notes"])

    def key_sort(item):
        tonic, mode = item[0]
        index = _KEY_SIGNATURE_TONIC_INDEX.get(tonic, len(_KEY_SIGNATURE_TONIC_ORDER))
        return (index, (tonic or "").lower(), (mode or "").lower())

    for (tonic, mode), flags in sorted(seen_keys.items(), key=key_sort):
        label = " ".join(part for part in (_display_tonic(tonic), mode) if part)
        if flags["microtonal"]:
            label += " (microtonal)"
        if flags["has_notes"]:
            label += "*"
        song.key_signatures.append(label)

    for key in key_rows:
        tonic, mode = key["tonic"], key["mode"]
        label = (
            "atonal"
            if tonic is None and mode is None
            else " ".join(part for part in (_display_tonic(tonic), mode) if part)
        )
        if key["microtonal"]:
            label += " (microtonal)"
        song.key_signature_timeline.append(
            {
                "start_seconds": key["start_seconds"],
                "start_label": format_seconds(key["start_seconds"]) or "0:00",
                "label": label,
                "notes": key["notes"],
            }
        )

    seen_times: set[tuple[int | None, int | None]] = set()
    for signature in row["time_signatures"]:
        numerator = signature["numerator"]
        denominator = signature["denominator"]
        identity = (numerator, denominator)
        label = "mixed meter" if identity == (None, None) else f"{numerator}⁄{denominator}"
        if identity not in seen_times:
            seen_times.add(identity)
            song.time_signatures.append(label)
        song.time_signature_timeline.append(
            {
                "start_seconds": signature["start_seconds"],
                "start_label": format_seconds(signature["start_seconds"]) or "0:00",
                "label": label,
                "notes": signature["notes"],
            }
        )

    song.subgenres = row["subgenres"]
    return song


_SHOW_ENTRY_QUERY: LiteralString = """
WITH selected AS MATERIALIZED (
    SELECT song_show.song_id, song_show.show_id,
           song_show.running_order, song_show.id AS song_show_id
    FROM song_show
    WHERE song_show.show_id = %s
)
SELECT
    song.id, data.title, data.artist,
    COALESCE((
        SELECT JSONB_AGG(JSONB_BUILD_OBJECT(
            'full_name', artist.full_name,
            'display_name', CASE WHEN artist.number = 1 THEN artist.full_name
                ELSE artist.full_name || ' (' || artist.number || ')' END,
            'stage_name', credit.stage_name,
            'join', credit.join_phrase
        ) ORDER BY credit.position)
        FROM artist_credit AS credit
        JOIN artist ON artist.id = credit.artist_id
        WHERE credit.artist_credit_set_id = data.artist_credit_set_id
    ), '[]'::jsonb) AS artists,
    data.native_title,
    song.country_id, COALESCE(an.name, country.name) AS name,
    country.is_participating, country.cc3, an.flag_variant,
    data.submitter_id, song.year_id, song.entry_number,
    year.special_name, year.special_short_name, year.status AS year_status,
    selected.running_order
FROM selected
JOIN song ON song.id = selected.song_id
JOIN LATERAL (
    SELECT song_data.title,
           artist_credit_name(song_data.artist_credit_set_id) AS artist,
           song_data.artist_credit_set_id, song_data.native_title,
           song_data.submitter_id
    FROM song_data
    WHERE song_data.song_id = song.id
       OR (
           song_data.song_id IS NULL
           AND song_data.country_id = song.country_id
           AND song_data.year_id = song.year_id
           AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
       )
    ORDER BY song_data.created_at DESC, song_data.id DESC
    LIMIT 1
) AS data ON true
JOIN country ON country.id = song.country_id
LEFT JOIN year ON year.id = song.year_id
LEFT JOIN alternative_name AS an ON an.country_id = song.country_id
    AND (an.from_year_id IS NULL OR song.year_id >= an.from_year_id)
    AND (an.to_year_id IS NULL OR song.year_id <= an.to_year_id)
"""

_SHOW_LINEUP_SQL: LiteralString = (
    _SHOW_ENTRY_QUERY
    + """WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
ORDER BY selected.running_order, selected.song_show_id"""
)

_SHOW_REVEAL_SQL: LiteralString = (
    _SHOW_ENTRY_QUERY
    + """LEFT JOIN show_qualifier
  ON show_qualifier.target_show_id = selected.show_id
 AND show_qualifier.song_id = song.id
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
ORDER BY show_qualifier.source_show_id NULLS FIRST,
         show_qualifier.qualifier_order NULLS FIRST,
         selected.running_order, selected.song_show_id"""
)


def get_show_lineup(year: int | None, short_name: str) -> list[Song] | None:
    """Load lightweight entries in running order for ballots and predictions."""
    show = get_show_id(short_name, year)
    if not show:
        return None
    cursor = get_db().cursor()
    cursor.execute(_SHOW_LINEUP_SQL, (show.id,))
    return _songs_from_rows(cursor.fetchall())


def get_show_result_entries(
    year: int | None, short_name: str, *, result_mode: str = "official"
) -> list[Song] | None:
    """Load show entries with their cached result data."""
    show = get_show_id(short_name, year)
    if not show:
        return None
    cursor = get_db().cursor()
    cursor.execute(_SHOW_LINEUP_SQL, (show.id,))
    songs = _songs_from_rows(cursor.fetchall())
    _attach_show_results(songs, show.id, result_mode)
    return songs


def get_show_reveal_entries(year: int | None, short_name: str) -> list[Song] | None:
    """Load lightweight entries in qualifier-aware reveal order."""
    show = get_show_id(short_name, year)
    if not show:
        return None
    cursor = get_db().cursor()
    cursor.execute(_SHOW_REVEAL_SQL, (show.id,))
    return _songs_from_rows(cursor.fetchall())


def get_year_index_winners() -> dict[int, Song]:
    """Load every closed year's winner as one specialized bulk operation.

    ``country_year_results`` is authoritative for the winning song. Starting
    from that small set lets PostgreSQL hydrate only winners instead of
    expanding ``current_song`` once for every year on the index page.
    """
    sql: LiteralString = """
WITH RECURSIVE winners AS (
    SELECT cyr.year_id, MIN(cyr.song_id) AS song_id
    FROM country_year_results AS cyr
    JOIN year AS winner_year ON winner_year.id = cyr.year_id
    WHERE cyr.place = 1
      AND winner_year.status = 'closed'
    GROUP BY cyr.year_id
), progression_paths(source_show_id, target_show_id, distance) AS (
    SELECT source_show_id, target_show_id, 1
    FROM show_progression
    UNION ALL
    SELECT paths.source_show_id, progression.target_show_id, paths.distance + 1
    FROM progression_paths AS paths
    JOIN show_progression AS progression
      ON progression.source_show_id = paths.target_show_id
), show_tiers AS (
    SELECT show.id AS show_id, COALESCE(MAX(paths.distance), 0) + 1 AS tier
    FROM show
    LEFT JOIN progression_paths AS paths ON paths.source_show_id = show.id
    WHERE show.national_final_id IS NULL
    GROUP BY show.id
), ranked_results AS (
    SELECT csr.*,
           ROW_NUMBER() OVER (
               PARTITION BY csr.song_id
               ORDER BY show_tiers.tier, csr.place, csr.show_id
           ) AS result_number
    FROM country_show_results AS csr
    JOIN winners ON winners.song_id = csr.song_id
                AND winners.year_id = csr.year_id
    JOIN show_tiers ON show_tiers.show_id = csr.show_id
    WHERE csr.result_mode = 'official'
)
SELECT
    song.id, data.title,
    artist_credit_name(data.artist_credit_set_id) AS artist,
    COALESCE((
        SELECT JSONB_AGG(JSONB_BUILD_OBJECT(
            'full_name', artist.full_name,
            'display_name', CASE WHEN artist.number = 1 THEN artist.full_name
                ELSE artist.full_name || ' (' || artist.number || ')' END,
            'stage_name', credit.stage_name,
            'join', credit.join_phrase
        ) ORDER BY credit.position)
        FROM artist_credit AS credit
        JOIN artist ON artist.id = credit.artist_id
        WHERE credit.artist_credit_set_id = data.artist_credit_set_id
    ), '[]'::jsonb) AS artists,
    data.native_title,
    song.country_id, COALESCE(an.name, country.name) AS name,
    country.is_participating, country.cc3, an.flag_variant,
    COALESCE(status.is_placeholder, false) AS is_placeholder,
    COALESCE(status.approval_status, 'pending') AS approval_status,
    data.native_language_id, data.title_language_id,
    data.native_lyrics, data.romanized_lyrics, data.translated_lyrics,
    account.username, song.year_id, data.poster_link, data.vtt_link,
    data.video_link, data.duration, data.snippet_start, data.snippet_end,
    data.snippet2_start, data.snippet2_end,
    data.submitter_id, data.notes, data.sources, song.entry_number,
    year.special_name, year.special_short_name, year.status AS year_status,
    title_language.name AS title_language_name,
    title_language.tag AS title_language_tag,
    title_language.extlang AS title_language_extlang,
    title_language.region AS title_language_region,
    title_language.subvariant AS title_language_subvariant,
    title_language.suppress_script AS title_language_suppress_script,
    native_language.name AS native_language_name,
    native_language.tag AS native_language_tag,
    native_language.extlang AS native_language_extlang,
    native_language.region AS native_language_region,
    native_language.subvariant AS native_language_subvariant,
    native_language.suppress_script AS native_language_suppress_script,
    result.running_order,
    result.total_points AS result_total_points,
    result.total_votes_received AS result_total_votes,
    result.point_distribution AS result_point_distribution,
    result.max_pts AS result_max_pts,
    result.total_voters AS result_total_voters,
    result.max_possible_points AS result_max_possible_points,
    result.points_percentage AS result_points_percentage,
    result.adjusted_max_possible_points AS result_adjusted_max_possible_points,
    result.points_midpoint AS result_points_midpoint,
    result.adjusted_points_percentage AS result_adjusted_points_percentage,
    COALESCE(winner_song_show.penalty, 0) AS result_penalty
FROM winners
JOIN song ON song.id = winners.song_id
JOIN LATERAL (
    SELECT song_data.*
    FROM song_data
    WHERE song_data.song_id = song.id
       OR (
           song_data.song_id IS NULL
           AND song_data.country_id = song.country_id
           AND song_data.year_id = song.year_id
           AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
       )
    ORDER BY song_data.created_at DESC, song_data.id DESC
    LIMIT 1
) AS data ON true
LEFT JOIN LATERAL (
    SELECT song_status.approval_status, song_status.is_placeholder
    FROM song_status
    WHERE song_status.song_id = song.id
    ORDER BY song_status.created_at DESC, song_status.id DESC
    LIMIT 1
) AS status ON true
JOIN ranked_results AS result
  ON result.song_id = song.id AND result.result_number = 1
LEFT JOIN song_show AS winner_song_show
  ON winner_song_show.song_id = result.song_id
 AND winner_song_show.show_id = result.show_id
JOIN country ON country.id = song.country_id
JOIN year ON year.id = song.year_id
LEFT JOIN account ON account.id = data.submitter_id
LEFT JOIN language AS title_language ON title_language.id = data.title_language_id
LEFT JOIN language AS native_language ON native_language.id = data.native_language_id
LEFT JOIN alternative_name AS an ON an.country_id = song.country_id
    AND (an.from_year_id IS NULL OR song.year_id >= an.from_year_id)
    AND (an.to_year_id IS NULL OR song.year_id <= an.to_year_id)
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
ORDER BY song.year_id
"""
    cursor = get_db().cursor()
    cursor.execute(sql)
    songs = _songs_from_rows(cursor.fetchall())
    _attach_languages(songs)
    return {song.year.id: song for song in songs}


_YEAR_OVERVIEW_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + _CYR_JOIN
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND song.year_id = %s
  AND song.main_participant
ORDER BY """
    + _YEAR_PLACE_ORDER
)

_USER_SUBMISSION_HISTORY_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + _CYR_JOIN
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND data.submitter_id = %s
  AND song.year_id IS NOT NULL
ORDER BY song.year_id,"""
    + _YEAR_PLACE_ORDER
)


def get_year_overview_songs(year: int) -> list[Song]:
    """Load the complete song rows required by the year overview."""
    cursor = get_db().cursor()
    cursor.execute(_YEAR_OVERVIEW_SQL, (year,))
    songs = _songs_from_rows(cursor.fetchall())
    _attach_languages(songs)
    return songs


def get_user_submission_history(user_id: int) -> list[Song]:
    """Load the rich song rows used by a user's submissions page."""
    cursor = get_db().cursor()
    cursor.execute(_USER_SUBMISSION_HISTORY_SQL, (user_id,))
    songs = _songs_from_rows(cursor.fetchall())
    _attach_languages(songs)
    return songs


def get_user_submission_countries(
    user_id: int, year: int, *, main_only: bool = False
) -> list[Country]:
    """Load only the countries a user may represent on a year's ballot."""
    main_filter: LiteralString = " AND song.main_participant" if main_only else ""
    sql: LiteralString = (
        """
SELECT country.id, COALESCE(an.name, country.name) AS name,
       country.is_participating, country.cc3, an.flag_variant
FROM song
JOIN LATERAL (
    SELECT song_data.submitter_id, song_data.title,
           song_data.artist_credit_set_id
    FROM song_data
    WHERE song_data.song_id = song.id
       OR (
           song_data.song_id IS NULL
           AND song_data.country_id = song.country_id
           AND song_data.year_id = song.year_id
           AND song_data.entry_number IS NOT DISTINCT FROM song.entry_number
       )
    ORDER BY song_data.created_at DESC, song_data.id DESC
    LIMIT 1
) AS data ON true
JOIN country ON country.id = song.country_id
JOIN year ON year.id = song.year_id
LEFT JOIN country_year_results AS cyr ON cyr.song_id = song.id
LEFT JOIN alternative_name AS an ON an.country_id = song.country_id
    AND (an.from_year_id IS NULL OR song.year_id >= an.from_year_id)
    AND (an.to_year_id IS NULL OR song.year_id <= an.to_year_id)
WHERE data.submitter_id = %s
  AND song.year_id = %s
  AND data.title IS NOT NULL
  AND data.artist_credit_set_id IS NOT NULL"""
        + main_filter
        + """
ORDER BY CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
         country.name, song.id
"""
    )
    cursor = get_db().cursor()
    cursor.execute(sql, (user_id, year))
    countries: list[Country] = []
    seen: set[str] = set()
    for row in cursor.fetchall():
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        countries.append(
            Country(
                cc=row["id"],
                name=row["name"],
                is_participating=bool(row["is_participating"]),
                cc3=row["cc3"],
                flag_variant=row["flag_variant"],
            )
        )
    return countries


def get_show_results_for_songs(
    song_ids: list[int], *, result_mode: str = "official", include_year: bool = True
) -> dict[int, dict]:
    """Return published show results for a list of song IDs.

    Returns a dict keyed by song_id.  Each value is a dict with keys
    'f', 'sc', 'sf' (or absent when the entry didn't participate in
    that round).  Each present value is a dict with 'pts', 'place',
    and 'show_name'.  Only rows from fully-published shows
    (status = 'full') are included.
    """
    if not song_ids:
        return {}

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT csr.song_id,
               csr.short_name,
               csr.total_points AS pts,
               csr.place,
               csr.total_countries,
               csr.placement_percentage,
               csr.show_name,
               csr.special_qualifier
        FROM country_show_results csr
        JOIN show ON show.id = csr.show_id
        WHERE csr.song_id = ANY(%s)
          AND show.status = 'full'
          AND csr.result_mode = %s
        ORDER BY csr.song_id, csr.year_id, csr.short_name
    """,
        (song_ids, result_mode),
    )

    results: dict[int, dict] = {}
    for row in cursor.fetchall():
        sid = row["song_id"]
        if sid not in results:
            results[sid] = {}
        sn = row["short_name"]
        if sn == "f":
            key = "f"
        elif sn == "sc":
            key = "sc"
        elif sn and (sn == "sf" or sn.startswith("sf")):
            key = "sf"
        else:
            continue
        # Keep the first match per key (there should be at most one per type)
        if key not in results[sid]:
            results[sid][key] = {
                "pts": row["pts"],
                "place": row["place"],
                "total_countries": row["total_countries"],
                "placement_percentage": row["placement_percentage"],
                "show_name": row["show_name"],
                "short_name": row["short_name"],
                "special_qualifier": row["special_qualifier"],
            }

    if not include_year:
        return results

    # Year-level placements (only for closed years)
    cursor.execute(
        """
        SELECT cyr.song_id, cyr.place, cyr.total_countries, cyr.placement_percentage
        FROM country_year_results cyr
        JOIN year ON year.id = cyr.year_id
        WHERE cyr.song_id = ANY(%s)
          AND year.status = 'closed'
    """,
        (song_ids,),
    )
    for row in cursor.fetchall():
        sid = row["song_id"]
        if sid not in results:
            results[sid] = {}
        results[sid]["year"] = {
            "place": row["place"],
            "total_countries": row["total_countries"],
            "placement_percentage": row["placement_percentage"],
        }

    return results


_COUNTRY_HISTORY_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + _CYR_JOIN
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND (song.country_id = %(cc)s OR country.cc3 = %(cc)s)
  AND song.year_id IS NOT NULL
  AND song.main_participant
ORDER BY song.year_id,"""
    + _YEAR_PLACE_ORDER
)

_ARTIST_HISTORY_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + _CYR_JOIN
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND song.year_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM artist_credit AS linked_credit
      WHERE linked_credit.artist_credit_set_id = data.artist_credit_set_id
        AND linked_credit.artist_id = %(artist_id)s
  )
ORDER BY song.year_id,"""
    + _YEAR_PLACE_ORDER
)

_MAIN_ENTRY_DETAILS_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND (song.country_id = %(cc)s OR country.cc3 = %(cc)s)
  AND song.year_id = %(year)s
  AND song.main_participant
ORDER BY song.id
LIMIT 1"""
)

_NUMBERED_ENTRY_DETAILS_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND (song.country_id = %(cc)s OR country.cc3 = %(cc)s)
  AND song.year_id = %(year)s
  AND song.entry_number = %(entry)s
ORDER BY song.id
LIMIT 1"""
)

_SPECIAL_COUNTRY_ENTRIES_SQL: LiteralString = (
    "SELECT"
    + _SONG_COLUMNS
    + _SONG_JOINS
    + """
WHERE data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
  AND (song.country_id = %(cc)s OR country.cc3 = %(cc)s)
  AND song.year_id = %(year)s
  AND song.main_participant
ORDER BY song.entry_number, song.id"""
)


def get_country_history(code: str) -> list[Song]:
    """Load the rich rows displayed on one country's history page."""
    cursor = get_db().cursor()
    cursor.execute(_COUNTRY_HISTORY_SQL, {"cc": code})
    songs = _songs_from_rows(cursor.fetchall())
    _attach_languages(songs)
    return songs


def get_artist_history(artist_id: int) -> list[Song]:
    """Load the rich rows displayed on one artist's entry history page."""
    cursor = get_db().cursor()
    cursor.execute(_ARTIST_HISTORY_SQL, {"artist_id": artist_id})
    songs = _songs_from_rows(cursor.fetchall())
    _attach_languages(songs)
    return songs


def get_entry_details(year: int, code: str, *, entry_number: int | None = None) -> Song | None:
    """Load one entry and all metadata required by its detail page."""
    sql = _NUMBERED_ENTRY_DETAILS_SQL if entry_number is not None else _MAIN_ENTRY_DETAILS_SQL
    cursor = get_db().cursor()
    cursor.execute(sql, {"cc": code, "year": year, "entry": entry_number})
    songs = _songs_from_rows(cursor.fetchall())
    if not songs:
        return None
    return _enrich_entry_details(songs[0])


def get_special_country_entries(year: int, code: str) -> list[Song]:
    """Load the rows used to resolve a country's entries in one special."""
    cursor = get_db().cursor()
    cursor.execute(_SPECIAL_COUNTRY_ENTRIES_SQL, {"cc": code, "year": year})
    songs = _songs_from_rows(cursor.fetchall())
    _attach_languages(songs)
    return songs
