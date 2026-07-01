from dataclasses import dataclass
from functools import lru_cache, total_ordering
from typing import Any, LiteralString

from ..db import fetchone, get_db
from .lookups import get_show_id
from .timefmt import format_seconds
from .types import Country, Language, VoteData, Year


@total_ordering
@dataclass
class Song:
    id: int
    title: str
    artist: str
    country: Country
    year: Year
    entry_number: int
    placeholder: bool
    languages: list[Language]
    vote_data: VoteData | None
    submitter: str | None
    submitter_id: int | None
    native_title: str | None
    title_lang: Language | None
    native_lang: Language | None
    translated_lyrics: str | None
    latin_lyrics: str | None
    native_lyrics: str | None
    lyrics_notes: str | None
    video_link: str | None
    poster_link: str | None
    vtt_link: str | None
    duration: float | None
    recap_start: str | None
    recap_end: str | None
    sources: str | None
    hidden: bool = False

    def __init__(self, song: dict, *, show_id: int | None = None):
        year = Year(
            id=song["year_id"],
            special_name=song.get("special_name"),
            special_short_name=song.get("special_short_name"),
        )
        self._raw_init(
            id=song["id"],
            title=song["title"],
            native_title=song["native_title"],
            artist=song["artist"],
            video_link=song["video_link"],
            poster_link=song["poster_link"],
            vtt_link=song.get("vtt_link"),
            duration=song.get("duration"),
            recap_start=song["snippet_start"],
            recap_end=song["snippet_end"],
            country=Country(
                cc=song["country_id"],
                name=song["name"],
                is_participating=bool(song["is_participating"]),
                cc3=song["cc3"],
                flag_variant=song.get("flag_variant"),
            ),
            placeholder=bool(song["is_placeholder"]),
            year=year,
            entry_number=song["entry_number"],
            title_lang=song["title_language_id"],
            submitter_id=song["submitter_id"],
            native_lang=song["native_language_id"],
            translated_lyrics=song["translated_lyrics"],
            latin_lyrics=song["romanized_lyrics"],
            native_lyrics=song["native_lyrics"],
            lyrics_notes=song["notes"],
            sources=song["sources"],
            submitter=song["username"],
            show_id=show_id,
            ro=song.get("running_order"),
        )

    def _raw_init(
        self,
        *,
        id: int,
        title: str,
        native_title: str | None,
        artist: str,
        country: Country,
        year: Year,
        entry_number: int,
        poster_link: str | None,
        vtt_link: str | None,
        placeholder: bool,
        submitter: str | None,
        submitter_id: int | None,
        title_lang: int | None,
        native_lang: int | None,
        lyrics_notes: str | None,
        translated_lyrics: str | None,
        latin_lyrics: str | None,
        native_lyrics: str | None,
        video_link: str | None,
        duration: float | None = None,
        recap_start: int | None,
        recap_end: int | None,
        sources: str | None,
        languages: list["Language"] | None = None,
        show_id: int | None = None,
        ro: int | None = None,
    ):
        if languages is None:
            languages = []

        self.id = id
        self.title = title
        self.artist = artist
        self.country = country
        self.native_title = native_title
        self.year = year
        self.entry_number = entry_number
        self.languages = languages
        self.key_signatures: list[str] = []
        self.key_signature_timeline: list[dict] = []
        self.time_signatures: list[str] = []
        self.time_signature_timeline: list[dict] = []
        self.subgenres: list[str] = []
        self.placeholder = placeholder
        self.submitter = submitter
        self.submitter_id = submitter_id
        self.translated_lyrics = translated_lyrics
        self.latin_lyrics = latin_lyrics
        self.native_lyrics = native_lyrics
        self.lyrics_notes = lyrics_notes
        self.video_link = video_link
        self.poster_link = poster_link
        self.vtt_link = vtt_link
        self.duration = duration
        self.sources = sources
        self.recap_start = format_seconds(recap_start) if recap_start is not None else None
        self.recap_end = format_seconds(recap_end) if recap_end is not None else None
        # Raw integer seconds for client-side timeline overlays where
        # an "M:SS" string isn't useful.
        self.recap_start_seconds = recap_start
        self.recap_end_seconds = recap_end
        self.title_lang = get_language(title_lang) if title_lang else Language()
        self.native_lang = get_language(native_lang) if native_lang else Language()
        if show_id is not None and ro is not None:
            self.vote_data = get_votes_for_song(self.id, show_id, ro)
        elif ro is not None:
            self.vote_data = VoteData(ro, None, None, None)
        else:
            self.vote_data = None

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


def get_votes_for_song(song_id: int, show_id: int, ro: int) -> VoteData:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT csr.total_points, csr.total_votes_received, csr.point_distribution,
               csr.max_pts, csr.total_voters,
               COALESCE(ss.penalty, 0) AS penalty
        FROM country_show_results csr
        LEFT JOIN song_show ss ON ss.song_id = csr.song_id AND ss.show_id = csr.show_id
        WHERE csr.song_id = %s AND csr.show_id = %s
    """,
        (song_id, show_id),
    )

    row = cursor.fetchone()
    if row:
        res = VoteData(
            ro=ro,
            total_votes=row["total_votes_received"],
            max_pts=row["max_pts"],
            show_voters=row["total_voters"],
        )
        res.sum = row["total_points"]
        res.count = row["total_votes_received"]
        res.penalty = row["penalty"] or 0
        for pt_str, cnt in (row["point_distribution"] or {}).items():
            res.pts[int(pt_str)] = cnt
        return res

    # Fallback: query raw vote table if cache is not yet populated
    cursor.execute(
        """
        SELECT COUNT(*) AS c FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        WHERE song_id = %s AND show_id = %s
    """,
        (song_id, show_id),
    )
    count = fetchone(cursor)["c"]

    cursor.execute(
        """
        SELECT MAX(score) AS m FROM show
        JOIN point ON show.point_system_id = point.point_system_id
        WHERE show.id = %s
    """,
        (show_id,),
    )
    max_pts = fetchone(cursor)["m"]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT voter_id) AS c FROM vote_set
        WHERE show_id = %s
    """,
        (show_id,),
    )
    show_voters = fetchone(cursor)["c"]

    cursor.execute(
        """
        SELECT score FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        WHERE song_id = %s AND show_id = %s
        ORDER BY score
    """,
        (song_id, show_id),
    )
    res = VoteData(ro=ro, total_votes=count, max_pts=max_pts, show_voters=show_voters)
    for points in cursor.fetchall():
        pt = points["score"]
        res.sum += pt
        res.count += 1
        res.pts[pt] += 1
    return res


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


_KEY_SIGNATURE_TONIC_ORDER = (
    "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"
)
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
        SELECT tonic, mode, microtonal, notes
        FROM song_key_signature
        WHERE song_id = %s
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
        SELECT numerator, denominator
        FROM song_time_signature
        WHERE song_id = %s
        ORDER BY start_seconds
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
        FROM song_subgenre
        JOIN subgenre ON subgenre.id = song_subgenre.subgenre_id
        WHERE song_subgenre.song_id = %s
        ORDER BY song_subgenre.priority
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
        SELECT start_seconds, numerator, denominator, notes
        FROM song_time_signature
        WHERE song_id = %s
        ORDER BY start_seconds
    """,
        (song_id,),
    )
    rows: list[dict] = []
    for r in cursor.fetchall():
        num, den = r["numerator"], r["denominator"]
        if num is None and den is None:
            label = "mixed meter"
        else:
            label = f"{num}⁄{den}"
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
        SELECT start_seconds, tonic, mode, microtonal, notes
        FROM song_key_signature
        WHERE song_id = %s
        ORDER BY start_seconds
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
        FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_id = %s
        ORDER BY priority
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
        SELECT song_language.song_id,
               language.name, language.tag, language.extlang,
               language.region, language.subvariant, language.suppress_script
        FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_language.song_id = ANY(%s)
        ORDER BY song_language.song_id, song_language.priority
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
    song.id, song.title, song.artist, song.native_title,
    song.country_id, COALESCE(an.name, country.name) AS name,
    country.is_participating, country.cc3, an.flag_variant,
    song.is_placeholder, song.native_language_id, song.title_language_id,
    song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
    account.username, song.year_id, song.poster_link,
    song.video_link, song.duration, song.snippet_start, song.snippet_end,
    song.submitter_id, song.notes, song.sources, song.entry_number,
    year.special_name, year.special_short_name"""

_SONG_JOINS: LiteralString = """
FROM song
JOIN country ON song.country_id = country.id
LEFT JOIN year ON year.id = song.year_id
LEFT OUTER JOIN account ON song.submitter_id = account.id
LEFT JOIN alternative_name an ON an.country_id = song.country_id
    AND (an.from_year_id IS NULL OR song.year_id >= an.from_year_id)
    AND (an.to_year_id IS NULL OR song.year_id <= an.to_year_id)"""

_CYR_JOIN: LiteralString = """
LEFT JOIN country_year_results cyr ON cyr.song_id = song.id"""

_YEAR_PLACE_ORDER: LiteralString = """
    CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
    country.name"""


def _song_query(
    *,
    select: LiteralString = "",
    joins: LiteralString = "",
    where: LiteralString,
    order_by: LiteralString = "",
) -> LiteralString:
    sql: LiteralString = "SELECT" + _SONG_COLUMNS
    if select:
        sql += ",\n    " + select
    sql += _SONG_JOINS + joins
    sql += "\nWHERE " + where
    if order_by:
        sql += "\nORDER BY " + order_by
    return sql


def _load_songs(
    sql: LiteralString,
    params: tuple | dict[str, Any],
    *,
    show_id: int | None = None,
    select_languages: bool = False,
) -> list[Song]:
    cursor = get_db().cursor()
    cursor.execute(sql, params)
    songs = [Song(row, show_id=show_id) for row in cursor.fetchall()]
    if select_languages:
        languages_by_song = get_languages_for_songs([s.id for s in songs])
        for song in songs:
            song.languages = languages_by_song.get(song.id, [])
    return songs


def _enrich_song(song: Song) -> Song:
    song.languages = get_song_languages(song.id)
    song.key_signatures = get_song_key_signatures(song.id)
    song.key_signature_timeline = get_song_key_signature_timeline(song.id)
    song.time_signatures = get_song_time_signatures(song.id)
    song.time_signature_timeline = get_song_time_signature_timeline(song.id)
    song.subgenres = get_song_subgenres_display(song.id)
    return song


def get_show_songs(
    year: int | None,
    short_name: str,
    *,
    select_languages=False,
    select_votes=False,
    sort_reveal=False,
) -> list[Song] | None:
    data = get_show_id(short_name, year)
    if not data:
        return None
    show_id = data.id

    order_by: LiteralString = "song_show.running_order, song_show.id"
    if sort_reveal:
        order_by = "song_show.qualifier_order, " + order_by

    sql = _song_query(
        select="song_show.running_order",
        joins="""
JOIN song_show ON song.id = song_show.song_id
JOIN show ON song_show.show_id = show.id""",
        where="show.id = %s",
        order_by=order_by,
    )
    return _load_songs(
        sql,
        (show_id,),
        show_id=show_id if select_votes else None,
        select_languages=select_languages,
    )


def get_show_winner(year: int | None, show: str) -> Song | None:
    songs = get_show_songs(year, show, select_votes=True)
    if not songs:
        return None

    songs.sort(reverse=True)
    winner = songs[0]
    winner.languages = get_song_languages(winner.id)

    return winner


def get_year_winner(year: int) -> Song | None:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT status FROM year
        WHERE id = %s
        """,
        (year,),
    )

    if fetchone(cursor)["status"] != "closed":
        return None

    return get_show_winner(year, "f")


def get_special_winner(show: str, year: int) -> Song | None:
    return get_show_winner(year, show)


def get_year_songs(year: int, *, select_languages=False) -> list[Song]:
    sql = _song_query(
        joins=_CYR_JOIN,
        where="song.year_id = %s",
        order_by=_YEAR_PLACE_ORDER,
    )
    return _load_songs(sql, (year,), select_languages=select_languages)


def get_user_songs(user_id: int, year: int | None = None, *, select_languages=False) -> list[Song]:
    where: LiteralString = "song.submitter_id = %(user_id)s AND song.year_id IS NOT NULL"
    params: dict[str, Any] = {"user_id": user_id}
    if year:
        where += " AND song.year_id = %(year)s"
        params["year"] = year

    sql = _song_query(
        joins=_CYR_JOIN,
        where=where,
        order_by="song.year_id," + _YEAR_PLACE_ORDER,
    )
    return _load_songs(sql, params, select_languages=select_languages)


def get_show_results_for_songs(song_ids: list[int]) -> dict[int, dict]:
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
               csr.show_name
        FROM country_show_results csr
        JOIN show ON show.id = csr.show_id
        WHERE csr.song_id = ANY(%s)
          AND show.status = 'full'
        ORDER BY csr.song_id, csr.year_id, csr.short_name
    """,
        (song_ids,),
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
            }

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


def get_country_songs(code: str, *, select_languages=False) -> list[Song]:
    sql = _song_query(
        joins=_CYR_JOIN,
        where="(song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id IS NOT NULL",
        order_by="song.year_id," + _YEAR_PLACE_ORDER,
    )
    return _load_songs(sql, {"cc": code}, select_languages=select_languages)


def get_song(year: int, code: str, *, select_results=False) -> Song | None:
    sql = _song_query(
        where="(song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id = %(year)s",
        order_by="song.year_id, country.name",
    )
    songs = _load_songs(sql, {"cc": code, "year": year})
    if not songs:
        return None
    return _enrich_song(songs[0])


def get_special_songs_for_country(year: int, code: str) -> list[Song]:
    """Get all songs for a country in a special (negative year_id)."""
    sql = _song_query(
        where="(song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id = %(year)s",
        order_by="song.entry_number",
    )
    return _load_songs(sql, {"cc": code, "year": year}, select_languages=True)


def get_special_song(year: int, code: str, entry_number: int) -> Song | None:
    """Get a specific song by country and entry_number in a special."""
    sql = _song_query(
        where="""(song.country_id = %(cc)s OR country.cc3 = %(cc)s)
    AND song.year_id = %(year)s AND song.entry_number = %(entry)s""",
    )
    songs = _load_songs(sql, {"cc": code, "year": year, "entry": entry_number})
    if not songs:
        return None
    return _enrich_song(songs[0])
