import contextlib
import json
import unicodedata

from flask import Blueprint, make_response, redirect, request, url_for
from psycopg import sql

from world_stage.db import fetchone, get_db
from world_stage.media import duration_for_link
from world_stage.messaging import (
    create_spot_watch_notifications,
    notify_new_message,
    notify_placeholder_claim,
)
from world_stage.utils import (
    ErrorID,
    err,
    format_seconds,
    parse_seconds,
    require_api_auth,
    resolve_country_code,
    resp,
)
from world_stage.utils.artists import (
    ArtistValidationError,
    create_artist_credit_set,
    fetch_artist_credits,
    parse_artist_credits,
    render_artist_credits,
)
from world_stage.utils.song_revisions import (
    MAX_YEAR_SUBMISSIONS,
    NonPlaceholderLimitError,
    create_song_revision,
    set_song_status,
    withdraw_song,
)


def _resolve_year_token(token) -> int | None:
    """Accept an int (regular or negative year ID) or a special short name
    and return the numeric year ID, or None if not found."""
    try:
        return int(token)
    except (ValueError, TypeError):
        pass
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM year WHERE special_short_name = %s", (str(token),))
    row = cursor.fetchone()
    return row["id"] if row else None


def _resolve_country_token(token: str) -> str | None:
    code = token.upper()
    return code if len(code) == 2 else resolve_country_code(code)


bp = Blueprint("song", __name__, url_prefix="/song")

# ── Constants ────────────────────────────────────────────────────────
ENGLISH_LANG_ID = 20
MAX_SNIPPET_DURATION = 20
MAX_SNIPPET2_DURATION = 10
MAX_ENTRY_CODE_LENGTH = 10
MAX_USER_SUBMISSIONS = 2
MAX_USER_SUBMISSIONS_SPECIAL = 1


@bp.get("/artists")
def search_artists():
    """Return canonical artists matching a name or previous stage name."""
    query = _normalize_text(request.args.get("q"))
    if not query:
        return resp([])
    if len(query) > 100:
        return err(ErrorID.BAD_REQUEST, "Artist search must be at most 100 characters")

    cursor = get_db().cursor()
    pattern = f"%{query}%"
    cursor.execute(
        """
        SELECT artist.id, artist.full_name, artist.native_name, artist.number,
               CASE WHEN artist.number = 1 THEN artist.full_name
                    ELSE artist.full_name || ' (' || artist.number || ')'
               END AS display_name,
               COALESCE(aliases.stage_names, ARRAY[]::text[]) AS stage_names
        FROM artist
        LEFT JOIN LATERAL (
            SELECT ARRAY_AGG(DISTINCT credit.stage_name ORDER BY credit.stage_name)
                   AS stage_names
            FROM artist_credit AS credit
            WHERE credit.artist_id = artist.id
        ) AS aliases ON true
        WHERE artist.full_name ILIKE %s
           OR artist.native_name ILIKE %s
           OR EXISTS (
                SELECT 1 FROM artist_credit AS credit
                WHERE credit.artist_id = artist.id
                  AND credit.stage_name ILIKE %s
           )
        ORDER BY
            CASE
                WHEN LOWER(artist.full_name) = LOWER(%s) THEN 0
                WHEN artist.full_name ILIKE %s THEN 1
                ELSE 2
            END,
            artist.full_name,
            artist.number,
            artist.id
        LIMIT 12
        """,
        (pattern, pattern, pattern, query, f"{query}%"),
    )
    return resp(cursor.fetchall())

MUTABLE_TEXT_FIELDS = (
    "title",
    "native_title",
    "artist",
    "video_link",
    "poster_link",
    "vtt_link",
    "snippet_start",
    "snippet_end",
    "snippet2_start",
    "snippet2_end",
    "translated_lyrics",
    "romanized_lyrics",
    "native_lyrics",
    "notes",
    "sources",
)


def _is_special_year(year: int) -> bool:
    return year < 0


REQUIRED_FIELDS = {
    "artist": "Artist",
    "title": "Title",
    "sources": "Sources",
}
REQUIRED_IDENTITY_FIELDS = {
    field: REQUIRED_FIELDS[field] for field in ("artist", "title")
}


def _remove_submitter_spot_watches(
    cursor,
    submitter_id: int | None,
    year_id: int,
    country_id: str,
) -> None:
    if submitter_id is None:
        return
    cursor.execute(
        """
        DELETE FROM year_spot_watch
        WHERE account_id = %s
          AND year_id = %s
          AND country_id = %s
        """,
        (submitter_id, year_id, country_id),
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _normalize_text(value) -> str | None:
    if value is None or value == "":
        return None
    value = str(value).strip()
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r", "")
    return None if value == "" else value


def _parse_entry_code(data: dict, permissions) -> tuple[str | None, tuple | None]:
    """Validate an explicitly supplied admin entry code."""
    if "entry_code" not in data:
        return None, None
    if not permissions.can_edit:
        return None, err(ErrorID.FORBIDDEN, "Only admins can set entry codes")
    entry_code = _normalize_text(data["entry_code"])
    if entry_code is not None and len(entry_code) > MAX_ENTRY_CODE_LENGTH:
        return None, err(
            ErrorID.BAD_REQUEST,
            f"entry_code must be at most {MAX_ENTRY_CODE_LENGTH} characters",
        )
    return entry_code, None


def _parse_request_artists(data: dict) -> tuple[list[dict] | None, str | None]:
    try:
        return parse_artist_credits(data), None
    except ArtistValidationError as exc:
        return None, str(exc)


def _same_artist_credits(incoming: list[dict], current: list[dict]) -> bool:
    if len(incoming) != len(current):
        return False
    for new, old in zip(incoming, current, strict=True):
        if new["stage_name"] != old["stage_name"] or new["join"] != old["join"]:
            return False
        if new["id"] is not None:
            if (
                new["id"] != old["id"]
                or (new["full_name"] or "").casefold()
                != old["full_name"].casefold()
                or new["number"] != old["number"]
            ):
                return False
        elif (
            (new["full_name"] or "").casefold() != old["full_name"].casefold()
            or new["number"] != old["number"]
            or (new["native_name"] or "").casefold()
            != (old["native_name"] or "").casefold()
        ):
            return False
    return True


def _form_bool(value: str | None) -> bool:
    """Interpret a form-encoded boolean (on/off, true/false, 1/0)."""
    return value in ("on", "true", "1", "yes")


def _snippet_duration_error(
    values: dict, start_field: str, end_field: str, maximum: int, label: str
) -> str | None:
    start = values.get(start_field)
    end = values.get(end_field)
    if start is None or end is None:
        return None
    start_seconds = start if isinstance(start, int) else parse_seconds(start)
    end_seconds = end if isinstance(end, int) else parse_seconds(end)
    if (
        start_seconds is not None
        and end_seconds is not None
        and end_seconds - start_seconds > maximum
    ):
        return f"{label} duration ({end_seconds - start_seconds}s) exceeds maximum ({maximum}s)"
    return None


def _get_request_data() -> tuple[dict | None, bool]:
    """Extract request data from JSON or form body.

    Returns (data_dict, is_form).  data_dict is None when the body
    cannot be parsed at all.

    For JSON requests the dict is returned as-is.
    For form requests the dict is normalised so that downstream code
    can treat both formats identically:
      - ``languageN`` keys → ``languages`` list[int]
      - ``on``/``off`` booleans → Python bools
    """
    ct = request.content_type or ""

    # ── JSON ─────────────────────────────────────────────────────
    if "json" in ct:
        return request.get_json(silent=True), False

    # ── Form ─────────────────────────────────────────────────────
    if "form" in ct:
        form = request.form.to_dict()
        if not form:
            return None, True

        data: dict = {}

        # Languages: language=id&language=id&…
        raw_langs = request.form.getlist("language")
        if raw_langs:
            lang_ids: list[int] = []
            for val in raw_langs:
                with contextlib.suppress(ValueError, TypeError):
                    lang_ids.append(int(val))
            if lang_ids:
                data["languages"] = lang_ids

        # Booleans
        for field in ("is_placeholder", "is_translation", "does_match"):
            if field in form:
                data[field] = _form_bool(form.get(field))
        # Scalars
        for field in ("year", "country", "submitter_id", "entry_number", "entry_code"):
            if field in form:
                data[field] = form[field]

        # Text fields – always included when present in form
        for field in MUTABLE_TEXT_FIELDS:
            if field in form:
                data[field] = form[field]

        return data, True

    return None, False


def _parse_languages(data: dict) -> tuple[list[int], list[str]]:
    """Return (language_ids, errors)."""
    errors: list[str] = []
    raw = data.get("languages", [])
    if not isinstance(raw, list):
        return [], ["languages must be a list of language IDs"]
    try:
        ids = [int(x) for x in raw]
    except (ValueError, TypeError):
        return [], ["Each language ID must be an integer"]
    if not ids:
        errors.append("At least one language must be provided")
    if len(ids) != len(set(ids)):
        errors.append("Each language may only appear once")
    return ids, errors


def _get_or_create_language_set(cursor, language_ids: list[int]) -> int:
    cursor.execute(
        """
        INSERT INTO language_set (language_ids)
        VALUES (%s)
        ON CONFLICT (language_ids) DO UPDATE
        SET language_ids = EXCLUDED.language_ids
        RETURNING id
        """,
        (language_ids,),
    )
    language_set_id = cursor.fetchone()["id"]
    cursor.execute(
        """
        INSERT INTO language_set_language (
            language_set_id, language_id, priority
        )
        SELECT %s, member.language_id, member.ordinality - 1
        FROM unnest(%s::bigint[]) WITH ORDINALITY
            AS member(language_id, ordinality)
        ON CONFLICT DO NOTHING
        """,
        (language_set_id, language_ids),
    )
    return language_set_id


def _get_or_create_genre_set(cursor, subgenre_ids: list[int]) -> int | None:
    if not subgenre_ids:
        return None
    cursor.execute(
        """
        INSERT INTO genre_set (subgenre_ids)
        VALUES (%s)
        ON CONFLICT (subgenre_ids) DO UPDATE
        SET subgenre_ids = EXCLUDED.subgenre_ids
        RETURNING id
        """,
        (subgenre_ids,),
    )
    set_id = cursor.fetchone()["id"]
    cursor.execute(
        """
        INSERT INTO genre_set_subgenre (genre_set_id, subgenre_id, priority)
        SELECT %s, member.subgenre_id, member.ordinality - 1
        FROM unnest(%s::bigint[]) WITH ORDINALITY
            AS member(subgenre_id, ordinality)
        ON CONFLICT DO NOTHING
        """,
        (set_id, subgenre_ids),
    )
    return set_id


def _get_or_create_key_signature_set(cursor, rows: list[dict]) -> int | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda row: row["start_seconds"])
    payload = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    cursor.execute(
        """
        INSERT INTO key_signature_set (signatures)
        VALUES (%s::jsonb)
        ON CONFLICT (signatures) DO UPDATE
        SET signatures = EXCLUDED.signatures
        RETURNING id
        """,
        (payload,),
    )
    set_id = cursor.fetchone()["id"]
    for priority, row in enumerate(rows):
        cursor.execute(
            """
            INSERT INTO key_signature_set_key_signature (
                key_signature_set_id, priority, start_seconds, tonic,
                mode, microtonal, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (set_id, priority, row["start_seconds"], row["tonic"], row["mode"],
             row["microtonal"], row.get("notes")),
        )
    return set_id


def _get_or_create_time_signature_set(cursor, rows: list[dict]) -> int | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda row: row["start_seconds"])
    payload = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    cursor.execute(
        """
        INSERT INTO time_signature_set (signatures)
        VALUES (%s::jsonb)
        ON CONFLICT (signatures) DO UPDATE
        SET signatures = EXCLUDED.signatures
        RETURNING id
        """,
        (payload,),
    )
    set_id = cursor.fetchone()["id"]
    for priority, row in enumerate(rows):
        cursor.execute(
            """
            INSERT INTO time_signature_set_time_signature (
                time_signature_set_id, priority, start_seconds,
                numerator, denominator, notes
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (set_id, priority, row["start_seconds"], row["numerator"],
             row["denominator"], row.get("notes")),
        )
    return set_id


def _resolve_language_ids(
    language_ids: list[int],
    is_translation: bool,
    does_match: bool,
    native_title: str | None,
) -> tuple[int | None, int | None]:
    """Derive title_language_id and native_language_id from the language list."""
    if not language_ids:
        return None, None
    native_language_id = language_ids[0] if (does_match or not native_title) else None
    title_language_id = ENGLISH_LANG_ID if is_translation else native_language_id
    return title_language_id, native_language_id


def _song_row_to_json(
    row: dict,
    languages: list[dict],
    key_signatures: list[dict] | None = None,
    time_signatures: list[dict] | None = None,
    subgenres: list[dict] | None = None,
    artists: list[dict] | None = None,
) -> dict:
    """Turn a DB row + language list into the API response body."""
    artist_items = artists if artists is not None else row.get("artists", [])
    return {
        "id": row["id"],
        "year": row["year_id"],
        "entry_number": row.get("entry_number"),
        "entry_code": row.get("entry_code"),
        "special_short_name": row.get("special_short_name"),
        "country_id": row["country_id"],
        "country_name": row["country_name"],
        "title": row["title"],
        "native_title": row["native_title"],
        "artist": row["artist"],
        "artists": artist_items,
        "is_placeholder": row["is_placeholder"],
        "title_language_id": row["title_language_id"],
        "native_language_id": row["native_language_id"],
        "video_link": row["video_link"],
        "duration": row["duration"],
        "poster_link": row["poster_link"],
        "vtt_link": row["vtt_link"],
        "snippet_start": (
            format_seconds(row["snippet_start"]) if row["snippet_start"] is not None else None
        ),
        "snippet_end": (
            format_seconds(row["snippet_end"]) if row["snippet_end"] is not None else None
        ),
        "snippet2_start": (
            format_seconds(row["snippet2_start"]) if row["snippet2_start"] is not None else None
        ),
        "snippet2_end": (
            format_seconds(row["snippet2_end"]) if row["snippet2_end"] is not None else None
        ),
        "translated_lyrics": row["translated_lyrics"],
        "romanized_lyrics": row["romanized_lyrics"],
        "native_lyrics": row["native_lyrics"],
        "notes": row["notes"],
        "sources": row["sources"],
        "submitter_id": row["submitter_id"],
        "submitter_name": row.get("username"),
        "languages": languages,
        "key_signatures": key_signatures or [],
        "time_signatures": time_signatures or [],
        "subgenres": subgenres or [],
    }


def _fetch_song(cursor, song_id: int) -> dict | None:
    cursor.execute(
        """
        SELECT song.id, song.year_id, song.country_id, country.name AS country_name,
               song.entry_code,
               song.title, song.native_title, song.artist, song.is_placeholder,
               song.title_language_id, song.native_language_id,
               song.language_set_id, song.genre_set_id,
               song.key_signature_set_id, song.time_signature_set_id,
               song.video_link, song.poster_link, song.vtt_link,
               song.snippet_start, song.snippet_end,
               song.snippet2_start, song.snippet2_end,
               song.translated_lyrics, song.romanized_lyrics, song.native_lyrics,
               song.notes, song.sources,
               song.submitter_id, account.username, song.entry_number,
               song.duration, song.song_data_id, song.approval_status,
               song.artist_credit_set_id,
               year.special_short_name
        FROM current_song AS song
        JOIN country ON song.country_id = country.id
        LEFT JOIN year ON year.id = song.year_id
        LEFT JOIN account ON song.submitter_id = account.id
        WHERE song.id = %s
    """,
        (song_id,),
    )
    return cursor.fetchone()


def _can_edit_national_final_song(cursor, song_id: int, user_id: int) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM national_final_song
        JOIN national_final
          ON national_final.id = national_final_song.national_final_id
        WHERE national_final_song.song_id = %s
          AND national_final.owner_id = %s
        """,
        (song_id, user_id),
    )
    return cursor.fetchone() is not None


_SONG_LIST_QUERY = sql.SQL(
    """
    WITH selected_song AS MATERIALIZED (
        SELECT song.*
        FROM song
        WHERE {selection}
    )
    SELECT
        song.id,
        song.year_id,
        song.country_id,
        song.entry_code,
        country.name AS country_name,
        data.title,
        data.native_title,
        artist_credit_name(data.artist_credit_set_id) AS artist,
        data.artist_credit_set_id,
        COALESCE(status.is_placeholder, false) AS is_placeholder,
        data.title_language_id,
        data.native_language_id,
        data.video_link,
        data.poster_link,
        data.vtt_link,
        data.snippet_start,
        data.snippet_end,
        data.snippet2_start,
        data.snippet2_end,
        data.translated_lyrics,
        data.romanized_lyrics,
        data.native_lyrics,
        data.notes,
        data.sources,
        data.submitter_id,
        account.username,
        song.entry_number,
        data.duration,
        year.special_short_name,
        COALESCE(languages.items, '[]'::jsonb) AS languages,
        COALESCE(key_signatures.items, '[]'::jsonb) AS key_signatures,
        COALESCE(time_signatures.items, '[]'::jsonb) AS time_signatures,
        COALESCE(subgenres.items, '[]'::jsonb) AS subgenres,
        COALESCE(artists.items, '[]'::jsonb) AS artists
    FROM selected_song song
    JOIN LATERAL (
        SELECT revision.*
        FROM song_data revision
        WHERE revision.song_id = song.id
           OR (
               revision.song_id IS NULL
               AND revision.country_id = song.country_id
               AND revision.year_id = song.year_id
               AND revision.entry_number IS NOT DISTINCT FROM song.entry_number
           )
        ORDER BY revision.created_at DESC, revision.id DESC
        LIMIT 1
    ) data ON data.title IS NOT NULL AND data.artist_credit_set_id IS NOT NULL
    LEFT JOIN LATERAL (
        SELECT revision_status.is_placeholder
        FROM song_status revision_status
        WHERE revision_status.song_id = song.id
        ORDER BY revision_status.created_at DESC, revision_status.id DESC
        LIMIT 1
    ) status ON true
    JOIN country ON country.id = song.country_id
    LEFT JOIN year ON year.id = song.year_id
    LEFT JOIN account ON account.id = data.submitter_id
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object('id', language.id, 'name', language.name)
            ORDER BY member.priority
        ) AS items
        FROM language_set_language member
        JOIN language ON language.id = member.language_id
        WHERE member.language_set_id = data.language_set_id
    ) languages ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'start_seconds', member.start_seconds,
                'tonic', member.tonic,
                'mode', member.mode,
                'microtonal', member.microtonal,
                'notes', member.notes
            ) ORDER BY member.priority
        ) AS items
        FROM key_signature_set_key_signature member
        WHERE member.key_signature_set_id = data.key_signature_set_id
    ) key_signatures ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'start_seconds', member.start_seconds,
                'numerator', member.numerator,
                'denominator', member.denominator,
                'notes', member.notes
            ) ORDER BY member.priority
        ) AS items
        FROM time_signature_set_time_signature member
        WHERE member.time_signature_set_id = data.time_signature_set_id
    ) time_signatures ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'id', subgenre.id,
                'name', subgenre.name,
                'genre_id', genre.id,
                'genre_name', genre.name
            ) ORDER BY member.priority
        ) AS items
        FROM genre_set_subgenre member
        JOIN subgenre ON subgenre.id = member.subgenre_id
        JOIN genre ON genre.id = subgenre.genre_id
        WHERE member.genre_set_id = data.genre_set_id
    ) subgenres ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'id', artist.id,
                'full_name', artist.full_name,
                'native_name', artist.native_name,
                'number', artist.number,
                'display_name', CASE WHEN artist.number = 1 THEN artist.full_name
                    ELSE artist.full_name || ' (' || artist.number || ')' END,
                'stage_name', member.stage_name,
                'join', member.join_phrase
            ) ORDER BY member.position
        ) AS items
        FROM artist_credit member
        JOIN artist ON artist.id = member.artist_id
        WHERE member.artist_credit_set_id = data.artist_credit_set_id
    ) artists ON true
    ORDER BY song.year_id, country.name, song.entry_number
    """
)


def _fetch_year_song_list(cursor, year_id: int) -> list[dict]:
    cursor.execute(
        _SONG_LIST_QUERY.format(selection=sql.SQL("song.year_id = %s")),
        (year_id,),
    )
    return cursor.fetchall()


def _fetch_country_song_list(cursor, country_id: str) -> list[dict]:
    cursor.execute(
        _SONG_LIST_QUERY.format(selection=sql.SQL("song.country_id = %s")),
        (country_id,),
    )
    return cursor.fetchall()


def _fetch_country_year_song_list(
    cursor,
    country_id: str,
    year_id: int,
    entry_number: int | None = None,
) -> list[dict]:
    selection = sql.SQL("song.country_id = %s AND song.year_id = %s")
    params: tuple[object, ...] = (country_id, year_id)
    if entry_number is not None:
        selection += sql.SQL(" AND song.entry_number = %s")
        params += (entry_number,)
    cursor.execute(_SONG_LIST_QUERY.format(selection=selection), params)
    return cursor.fetchall()


def _song_list_rows_to_json(rows: list[dict]) -> list[dict]:
    return [
        _song_row_to_json(
            row,
            row["languages"],
            row["key_signatures"],
            row["time_signatures"],
            row["subgenres"],
        )
        for row in rows
    ]


def _fetch_song_languages(cursor, song_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT language.id, language.name
        FROM current_song AS song
        JOIN language_set_language AS member
          ON member.language_set_id = song.language_set_id
        JOIN language ON member.language_id = language.id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    return [{"id": r["id"], "name": r["name"]} for r in cursor.fetchall()]


def _fetch_song_artists(cursor, song_id: int) -> list[dict]:
    cursor.execute(
        "SELECT artist_credit_set_id FROM current_song WHERE id = %s",
        (song_id,),
    )
    row = cursor.fetchone()
    return fetch_artist_credits(cursor, row["artist_credit_set_id"] if row else None)


def _fetch_song_key_signatures(cursor, song_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT member.start_seconds, member.tonic, member.mode,
               member.microtonal, member.notes
        FROM current_song AS song
        JOIN key_signature_set_key_signature AS member
          ON member.key_signature_set_id = song.key_signature_set_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    return [
        {
            "start_seconds": r["start_seconds"],
            "tonic": r["tonic"],
            "mode": r["mode"],
            "microtonal": bool(r["microtonal"]),
            "notes": r["notes"],
        }
        for r in cursor.fetchall()
    ]


# Canonical tonic spellings. Enharmonic pairs collapse to their flat
# form (Db, Eb, Ab, Bb) except for F#/Gb, which collapses to the sharp
# form for historical/notation reasons.
_TONIC_CANONICAL = frozenset({"C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"})
_TONIC_ALIASES = {
    "C#": "Db",
    "D#": "Eb",
    "Gb": "F#",
    "G#": "Ab",
    "A#": "Bb",
    "Cb": "B",
    "Fb": "E",
    "B#": "C",
    "E#": "F",
}


def _normalize_mode(value) -> str | None:
    """Normalise a mode string: lowercase, collapse internal whitespace,
    and trim. Returns None for empty input. Free-form values (e.g. from
    the "Other" option) are preserved aside from this whitespace/case
    cleanup.
    """
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value)).replace("\r", "")
    s = " ".join(s.split()).lower()
    return s or None


def _normalize_tonic(value) -> str | None:
    """Normalise a tonic string into its canonical ASCII form.

    Accepts Unicode accidentals (♯, ♭, ♮) and case variants. Recognised
    sharp spellings collapse to their flat enharmonic (e.g. ``C#`` →
    ``Db``) except for ``F#``/``Gb`` which are both retained. Strings
    that don't match a standard tonic are passed through unchanged so
    the "Other" escape hatch (microtonality, alternative tunings) is
    preserved.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    candidate = raw.replace("♯", "#").replace("♭", "b").replace("♮", "")
    if len(candidate) == 1:
        candidate = candidate.upper()
    elif len(candidate) == 2:
        candidate = candidate[0].upper() + candidate[1].lower()

    if candidate in _TONIC_ALIASES:
        return _TONIC_ALIASES[candidate]
    if candidate in _TONIC_CANONICAL:
        return candidate
    return raw


def _parse_key_signatures(data: dict) -> tuple[list[dict] | None, list[str]]:
    """Validate and normalise the ``key_signatures`` field.

    Returns ``(rows, errors)``. ``rows`` is None when the field is absent
    (caller should leave existing rows untouched). An empty list means
    the caller asked to clear all key signatures. A row with both tonic
    and mode NULL represents an atonal section.
    """
    if "key_signatures" not in data:
        return None, []

    raw = data.get("key_signatures")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return None, ["key_signatures must be a list"]

    rows: list[dict] = []
    seen_starts: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, [f"key_signatures[{i}] must be an object"]

        try:
            start_seconds = int(item.get("start_seconds") or 0)
        except (ValueError, TypeError):
            return None, [f"key_signatures[{i}].start_seconds must be an integer"]
        if start_seconds < 0:
            return None, [f"key_signatures[{i}].start_seconds must be >= 0"]

        tonic = _normalize_tonic(item.get("tonic"))
        mode = _normalize_mode(item.get("mode"))
        microtonal = bool(item.get("microtonal", False))
        notes = _normalize_text(item.get("notes"))

        if start_seconds in seen_starts:
            return None, [
                f"key_signatures must have unique start_seconds (duplicate: {start_seconds})"
            ]
        seen_starts.add(start_seconds)

        rows.append(
            {
                "start_seconds": start_seconds,
                "tonic": tonic,
                "mode": mode,
                "microtonal": microtonal,
                "notes": notes,
            }
        )

    return rows, []


# ── Time signatures ──────────────────────────────────────────────────

_ALLOWED_DENOMINATORS = frozenset({1, 2, 4, 8, 16, 32})


def _fetch_song_time_signatures(cursor, song_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT member.start_seconds, member.numerator, member.denominator,
               member.notes
        FROM current_song AS song
        JOIN time_signature_set_time_signature AS member
          ON member.time_signature_set_id = song.time_signature_set_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    return [
        {
            "start_seconds": r["start_seconds"],
            "numerator": r["numerator"],
            "denominator": r["denominator"],
            "notes": r["notes"],
        }
        for r in cursor.fetchall()
    ]


def _parse_time_signatures(data: dict) -> tuple[list[dict] | None, list[str]]:
    """Validate and normalise the ``time_signatures`` field.

    Returns ``(rows, errors)``. ``rows`` is None when the field is
    absent (caller should leave existing rows untouched). An empty list
    means the caller asked to clear all time signatures. A row with
    both numerator and denominator NULL represents a mixed-meter
    section.
    """
    if "time_signatures" not in data:
        return None, []

    raw = data.get("time_signatures")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return None, ["time_signatures must be a list"]

    rows: list[dict] = []
    seen_starts: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, [f"time_signatures[{i}] must be an object"]

        try:
            start_seconds = int(item.get("start_seconds") or 0)
        except (ValueError, TypeError):
            return None, [f"time_signatures[{i}].start_seconds must be an integer"]
        if start_seconds < 0:
            return None, [f"time_signatures[{i}].start_seconds must be >= 0"]

        n_raw = item.get("numerator")
        d_raw = item.get("denominator")

        if n_raw is None and d_raw is None:
            numerator = None
            denominator = None
        else:
            try:
                numerator = int(n_raw) if n_raw is not None else None
                denominator = int(d_raw) if d_raw is not None else None
            except (ValueError, TypeError):
                return None, [f"time_signatures[{i}] numerator and denominator must be integers"]
            if numerator is None or denominator is None:
                return None, [
                    f"time_signatures[{i}] numerator and denominator must both be set or both null"
                ]
            if numerator <= 0:
                return None, [f"time_signatures[{i}].numerator must be > 0"]
            if denominator not in _ALLOWED_DENOMINATORS:
                return None, [
                    f"time_signatures[{i}].denominator must be one of "
                    f"{sorted(_ALLOWED_DENOMINATORS)}"
                ]

        notes = _normalize_text(item.get("notes"))

        if start_seconds in seen_starts:
            return None, [
                f"time_signatures must have unique start_seconds (duplicate: {start_seconds})"
            ]
        seen_starts.add(start_seconds)

        rows.append(
            {
                "start_seconds": start_seconds,
                "numerator": numerator,
                "denominator": denominator,
                "notes": notes,
            }
        )

    return rows, []


# ── Subgenres ────────────────────────────────────────────────────────


def _fetch_song_subgenres(cursor, song_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT subgenre.id, subgenre.name AS subgenre_name,
               genre.id AS genre_id, genre.name AS genre_name
        FROM current_song AS song
        JOIN genre_set_subgenre AS member
          ON member.genre_set_id = song.genre_set_id
        JOIN subgenre ON subgenre.id = member.subgenre_id
        JOIN genre ON genre.id = subgenre.genre_id
        WHERE song.id = %s
        ORDER BY member.priority
    """,
        (song_id,),
    )
    return [
        {
            "id": r["id"],
            "name": r["subgenre_name"],
            "genre_id": r["genre_id"],
            "genre_name": r["genre_name"],
        }
        for r in cursor.fetchall()
    ]


def _parse_subgenres(data: dict) -> tuple[list[int] | None, list[str]]:
    """Validate the ``subgenres`` field. Returns ``(ids, errors)``.

    ``ids`` is None when the field is absent (caller preserves existing
    rows). An empty list means clear all subgenres. Duplicates within
    the submitted list are silently deduped, preserving the first
    occurrence so the user-chosen ordering is kept.
    """
    if "subgenres" not in data:
        return None, []
    raw = data.get("subgenres")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return None, ["subgenres must be a list of subgenre IDs"]
    ids: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            sid = int(item)
        except (ValueError, TypeError):
            return None, ["Each subgenre ID must be an integer"]
        if sid in seen:
            continue
        seen.add(sid)
        ids.append(sid)
    return ids, []


def _validate_country(country_code: str) -> tuple[str | None, tuple | None]:
    """Resolve & validate a country code. Returns (resolved_cc, error_response)."""
    cc = resolve_country_code(country_code.upper())
    if not cc:
        return None, err(ErrorID.NOT_FOUND, f"Country '{country_code}' not found")
    return cc, None


def _validate_year(cursor, year: int) -> tuple | None:
    """Return an error response if the year doesn't exist or isn't accepting
    new submissions, else None."""
    cursor.execute("SELECT id, submissions_open FROM year WHERE id = %s", (year,))
    row = cursor.fetchone()
    if not row:
        return err(ErrorID.NOT_FOUND, f"Year {year} not found")
    if not row["submissions_open"]:
        return err(ErrorID.FORBIDDEN, f"Year {year} is no longer accepting new submissions")
    return None


def _regular_submission_limit_error(
    cursor, user_id: int, year: int, *, exclude_song_id: int | None = None
) -> tuple | None:
    """Validate a non-placeholder regular-year entry against both limits."""
    exclusion = "" if exclude_song_id is None else "AND id <> %(exclude_song_id)s"
    params = {
        "user_id": user_id,
        "year": year,
        "exclude_song_id": exclude_song_id,
    }
    cursor.execute(
        f"""SELECT COUNT(*) AS c FROM current_song
            WHERE submitter_id = %(user_id)s AND year_id = %(year)s
              AND main_participant AND NOT is_placeholder {exclusion}""",
        params,
    )
    if fetchone(cursor)["c"] >= MAX_USER_SUBMISSIONS:
        return err(
            ErrorID.FORBIDDEN,
            f"You may submit at most {MAX_USER_SUBMISSIONS} songs per year",
        )

    cursor.execute(
        f"""SELECT COUNT(*) AS c FROM current_song
            WHERE year_id = %(year)s AND main_participant
              AND NOT is_placeholder {exclusion}""",
        params,
    )
    if fetchone(cursor)["c"] >= MAX_YEAR_SUBMISSIONS:
        return err(
            ErrorID.FORBIDDEN,
            f"This year already has the maximum number of entries ({MAX_YEAR_SUBMISSIONS})",
        )
    return None


# ── GET /api/song/<id> ───────────────────────────────────────────────


@bp.get("/<int:id>")
def get_song(id: int):
    db = get_db()
    cursor = db.cursor()

    row = _fetch_song(cursor, id)
    if not row:
        return err(ErrorID.NOT_FOUND, f"Song {id} not found")

    languages = _fetch_song_languages(cursor, id)
    key_signatures = _fetch_song_key_signatures(cursor, id)
    time_signatures = _fetch_song_time_signatures(cursor, id)
    subgenres = _fetch_song_subgenres(cursor, id)
    artists = _fetch_song_artists(cursor, id)
    return resp(
        _song_row_to_json(
            row, languages, key_signatures, time_signatures, subgenres, artists
        )
    )


# ── GET /api/song/<cc>/<year> ─────────────────────────────────────────


@bp.get("/<cc>/<year>")
def get_song_by_country(cc: str, year: str):
    canonical = _resolve_country_token(cc)
    if canonical and canonical.lower() != cc.lower():
        return redirect(
            url_for("api.song.get_song_by_country", cc=canonical.lower(), year=year), 301
        )

    year_id = _resolve_year_token(year)
    if year_id is None:
        return err(ErrorID.NOT_FOUND, f"Year '{year}' not found")

    db = get_db()
    cursor = db.cursor()

    if _is_special_year(year_id):
        # Specials can have multiple entries per country. An entry_number
        # query parameter disambiguates; without it, return the full list.
        entry_raw = request.args.get("entry_number")
        if entry_raw is not None:
            try:
                entry_number = int(entry_raw)
            except (ValueError, TypeError):
                return err(ErrorID.BAD_REQUEST, "entry_number must be an integer")
            rows = _fetch_country_year_song_list(
                cursor, canonical or cc.upper(), year_id, entry_number
            )
            row = rows[0] if rows else None
            if not row:
                return err(
                    ErrorID.NOT_FOUND,
                    f"No song found for {cc} in special {year} entry {entry_number}",
                )
            return resp(_song_list_rows_to_json([row])[0])

        rows = _fetch_country_year_song_list(cursor, canonical or cc.upper(), year_id)
        if not rows:
            return err(ErrorID.NOT_FOUND, f"No song found for {cc} in special {year}")
        return resp(_song_list_rows_to_json(rows))

    rows = _fetch_country_year_song_list(cursor, canonical or cc.upper(), year_id)
    row = rows[0] if rows else None
    if not row:
        return err(ErrorID.NOT_FOUND, f"No song found for {cc} in {year}")

    return resp(_song_list_rows_to_json([row])[0])


@bp.get("/<cc>/<year>/<int:entry_number>")
def get_song_by_country_entry(cc: str, year: str, entry_number: int):
    canonical = _resolve_country_token(cc)
    if canonical and canonical.lower() != cc.lower():
        return redirect(
            url_for(
                "api.song.get_song_by_country_entry",
                cc=canonical.lower(),
                year=year,
                entry_number=entry_number,
            ),
            301,
        )

    year_id = _resolve_year_token(year)
    if year_id is None:
        return err(ErrorID.NOT_FOUND, f"Year '{year}' not found")

    db = get_db()
    cursor = db.cursor()
    rows = _fetch_country_year_song_list(
        cursor, canonical or cc.upper(), year_id, entry_number
    )
    row = rows[0] if rows else None
    if not row:
        return err(ErrorID.NOT_FOUND, f"No song found for {cc} in {year} entry {entry_number}")
    return resp(_song_list_rows_to_json([row])[0])


# ── POST /api/song ───────────────────────────────────────────────────


@bp.post("")
@require_api_auth
def create_song(auth: tuple):
    user_id, _username, permissions = auth

    data, _is_form = _get_request_data()
    if not data:
        return err(ErrorID.BAD_REQUEST, "Request body must be JSON or form-encoded")

    # ── Required fields ──────────────────────────────────────────
    year = data.get("year")
    country_code = data.get("country")
    if not year or not country_code:
        return err(ErrorID.BAD_REQUEST, "year and country are required")

    try:
        year = int(year)
    except (ValueError, TypeError):
        return err(ErrorID.BAD_REQUEST, "year must be an integer")

    db = get_db()
    cursor = db.cursor()

    entry_code, entry_code_error = _parse_entry_code(data, permissions)
    if entry_code_error:
        return entry_code_error

    national_final_id = data.get("national_final_id")
    national_final = None
    if national_final_id is not None:
        try:
            national_final_id = int(national_final_id)
        except (TypeError, ValueError):
            return err(ErrorID.BAD_REQUEST, "national_final_id must be an integer")
        cursor.execute(
            "SELECT id, year_id, owner_id, owner_country_id, status "
            "FROM national_final WHERE id = %s",
            (national_final_id,),
        )
        national_final = cursor.fetchone()
        if not national_final:
            return err(ErrorID.NOT_FOUND, "National final not found")
        if national_final["status"] not in {"draft", "submissions"}:
            return err(ErrorID.BAD_REQUEST, "This national final is not accepting candidates")
        if not permissions.can_view_restricted and national_final["owner_id"] != user_id:
            return err(ErrorID.FORBIDDEN, "You do not manage this national final")
        if national_final["year_id"] != year:
            return err(ErrorID.BAD_REQUEST, "National final and song year do not match")
    else:
        year_err = _validate_year(cursor, year)
        if year_err:
            return year_err

    cc, cc_err = _validate_country(country_code)
    if cc_err:
        return cc_err
    if (
        national_final
        and national_final["owner_country_id"]
        and cc != national_final["owner_country_id"]
    ):
        return err(
            ErrorID.BAD_REQUEST,
            "This national final only accepts its owner country",
        )
    if national_final is None:
        cursor.execute(
            """
            SELECT 1 FROM national_final
            WHERE year_id = %s AND owner_country_id = %s
              AND status <> 'cancelled'
            """,
            (year, cc),
        )
        if cursor.fetchone():
            return err(
                ErrorID.FORBIDDEN,
                "This country is being selected through a national final",
            )

    is_placeholder = bool(data.get("is_placeholder", False))

    # ── Duplicate / entry_number handling ────────────────────────
    # A country may participate more than once. Withdrawn identities continue
    # to reserve their number, so a later submission is always the next entry.
    cursor.execute(
        "SELECT COALESCE(MAX(entry_number), 0) + 1 AS next FROM song "
        "WHERE year_id = %s AND country_id = %s",
        (year, cc),
    )
    entry_number = fetchone(cursor)["next"]

    # ── Submission limits (non-admins) ───────────────────────────
    if national_final is None and not permissions.can_edit:
        if _is_special_year(year):
            cursor.execute(
                "SELECT COUNT(*) AS c FROM current_song "
                "WHERE submitter_id = %s AND year_id = %s AND NOT is_placeholder",
                (user_id, year),
            )
            if fetchone(cursor)["c"] >= MAX_USER_SUBMISSIONS_SPECIAL:
                return err(
                    ErrorID.FORBIDDEN,
                    f"You may submit at most {MAX_USER_SUBMISSIONS_SPECIAL} song per special",
                )
        elif not is_placeholder:
            if limit_err := _regular_submission_limit_error(cursor, user_id, year):
                return limit_err

    # ── Parse body ───────────────────────────────────────────────
    language_ids, lang_errors = _parse_languages(data)
    if lang_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(lang_errors))

    key_signatures, ks_errors = _parse_key_signatures(data)
    if ks_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(ks_errors))

    time_signatures, ts_errors = _parse_time_signatures(data)
    if ts_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(ts_errors))

    subgenre_ids, sg_errors = _parse_subgenres(data)
    if sg_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(sg_errors))

    text = {k: _normalize_text(data.get(k)) for k in MUTABLE_TEXT_FIELDS}
    artist_credits, artist_error = _parse_request_artists(data)
    if artist_error:
        return err(ErrorID.BAD_REQUEST, artist_error)
    assert artist_credits is not None
    text["artist"] = render_artist_credits(artist_credits)

    is_translation = bool(data.get("is_translation", False))
    does_match = bool(data.get("does_match", False))
    title_language_id, native_language_id = _resolve_language_ids(
        language_ids,
        is_translation,
        does_match,
        text["native_title"],
    )

    # Artist and title distinguish live songs from deletion sentinels.
    for field, label in REQUIRED_IDENTITY_FIELDS.items():
        if not text.get(field):
            return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")

    # ── Validation (non-admins) ──────────────────────────────────
    if not permissions.can_view_restricted:
        for field, label in REQUIRED_FIELDS.items():
            if not text.get(field):
                return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")
        if text["snippet_start"] and text["snippet_end"]:
            s = parse_seconds(text["snippet_start"])
            e = parse_seconds(text["snippet_end"])
            if s is not None and e is not None and (e - s) > MAX_SNIPPET_DURATION:
                return err(
                    ErrorID.BAD_REQUEST,
                    f"Snippet duration ({e - s}s) exceeds maximum ({MAX_SNIPPET_DURATION}s)",
                )
    if message := _snippet_duration_error(
        text,
        "snippet2_start",
        "snippet2_end",
        MAX_SNIPPET2_DURATION,
        "Snippet 2",
    ):
        return err(ErrorID.BAD_REQUEST, message)

    # ── Submitter override (admins only) ─────────────────────────
    submitter_id = user_id
    if permissions.can_edit and "submitter_id" in data:
        raw = data["submitter_id"]
        if raw is None:
            submitter_id = None
        else:
            try:
                submitter_id = int(raw)
            except (ValueError, TypeError):
                return err(ErrorID.BAD_REQUEST, "submitter_id must be an integer or null")

    # ── Insert ───────────────────────────────────────────────────
    language_set_id = _get_or_create_language_set(cursor, language_ids)
    genre_set_id = _get_or_create_genre_set(cursor, subgenre_ids or [])
    key_signature_set_id = _get_or_create_key_signature_set(
        cursor, key_signatures or []
    )
    time_signature_set_id = _get_or_create_time_signature_set(
        cursor, time_signatures or []
    )
    artist_credit_set_id = create_artist_credit_set(cursor, artist_credits)

    cursor.execute(
        """
        INSERT INTO song (
            year_id, country_id, entry_number, entry_code, main_participant
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """,
        (year, cc, entry_number, entry_code, national_final is None),
    )
    song_id = fetchone(cursor)["id"]
    if national_final_id is not None:
        cursor.execute(
            "INSERT INTO national_final_song (national_final_id, song_id) VALUES (%s, %s)",
            (national_final_id, song_id),
        )
    cursor.execute(
        """
        INSERT INTO song_data (
            song_id, title, native_title, artist_credit_set_id,
            title_language_id, native_language_id, language_set_id,
            genre_set_id, key_signature_set_id, time_signature_set_id,
            video_link, duration,
            poster_link, vtt_link, snippet_start, snippet_end,
            snippet2_start, snippet2_end, translated_lyrics,
            romanized_lyrics, native_lyrics, submitter_id, notes, sources,
            changed_by
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            song_id, text["title"], text["native_title"], artist_credit_set_id,
            title_language_id, native_language_id, language_set_id,
            genre_set_id, key_signature_set_id, time_signature_set_id,
            text["video_link"], duration_for_link(text["video_link"]),
            text["poster_link"], text["vtt_link"],
            parse_seconds(text["snippet_start"]), parse_seconds(text["snippet_end"]),
            parse_seconds(text["snippet2_start"]), parse_seconds(text["snippet2_end"]),
            text["translated_lyrics"], text["romanized_lyrics"],
            text["native_lyrics"], submitter_id, text["notes"], text["sources"],
            user_id,
        ),
    )
    try:
        set_song_status(
            cursor, song_id, changed_by=user_id, is_placeholder=is_placeholder
        )
    except NonPlaceholderLimitError as exc:
        db.rollback()
        return err(ErrorID.FORBIDDEN, str(exc))

    # Once the user occupies this country/year slot, changes to their own
    # submission are no longer useful watch events. This applies equally to
    # regular entries and placeholders.
    _remove_submitter_spot_watches(cursor, submitter_id, year, cc)

    db.commit()

    row = _fetch_song(cursor, song_id)
    assert row is not None  # just inserted
    languages = _fetch_song_languages(cursor, song_id)
    ks = _fetch_song_key_signatures(cursor, song_id)
    ts = _fetch_song_time_signatures(cursor, song_id)
    sg = _fetch_song_subgenres(cursor, song_id)
    artists = _fetch_song_artists(cursor, song_id)
    body, code = resp(_song_row_to_json(row, languages, ks, ts, sg, artists), 201)
    response = make_response(body, code)
    response.headers["Location"] = url_for("api.song.get_song", id=song_id)
    return response


# ── PUT /api/song/<id> ───────────────────────────────────────────────


@bp.put("/<int:id>")
@require_api_auth
def replace_song(id: int, auth: tuple):
    user_id, username, permissions = auth

    data, _is_form = _get_request_data()
    if not data:
        return err(ErrorID.BAD_REQUEST, "Request body must be JSON or form-encoded")

    db = get_db()
    cursor = db.cursor()

    row = _fetch_song(cursor, id)
    if not row:
        return err(ErrorID.NOT_FOUND, f"Song {id} not found")

    entry_code, entry_code_error = _parse_entry_code(data, permissions)
    if entry_code_error:
        return entry_code_error
    entry_code_supplied = "entry_code" in data

    # ── Permission check ─────────────────────────────────────────
    # Non-admins may edit their own submissions, or claim a placeholder
    # belonging to someone else (which transfers ownership to them).
    is_claim = False
    can_edit_nf_song = _can_edit_national_final_song(cursor, id, user_id)
    if (
        not permissions.can_edit
        and row["submitter_id"] != user_id
        and not can_edit_nf_song
    ):
        if row["is_placeholder"]:
            is_claim = True
        else:
            return err(ErrorID.FORBIDDEN, "You can only edit your own submissions")

    # Claiming a placeholder is effectively a new submission; require the
    # year to still be open.
    if is_claim:
        cursor.execute(
            "SELECT submissions_open FROM year WHERE id = %s", (row["year_id"],)
        )
        year_row = cursor.fetchone()
        if year_row and not year_row["submissions_open"]:
            return err(ErrorID.FORBIDDEN, "This year is no longer accepting new submissions")

    # ── Parse languages (required) ───────────────────────────────
    language_ids, lang_errors = _parse_languages(data)
    if lang_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(lang_errors))

    key_signatures, ks_errors = _parse_key_signatures(data)
    if ks_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(ks_errors))

    time_signatures, ts_errors = _parse_time_signatures(data)
    if ts_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(ts_errors))

    subgenre_ids, sg_errors = _parse_subgenres(data)
    if sg_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(sg_errors))

    # ── Build full replacement values ────────────────────────────
    text = {k: _normalize_text(data.get(k)) for k in MUTABLE_TEXT_FIELDS}
    artist_credits, artist_error = _parse_request_artists(data)
    if artist_error:
        return err(ErrorID.BAD_REQUEST, artist_error)
    assert artist_credits is not None
    text["artist"] = render_artist_credits(artist_credits)

    is_placeholder = bool(data.get("is_placeholder", False))
    is_translation = bool(data.get("is_translation", False))
    does_match = bool(data.get("does_match", False))

    if (
        not permissions.can_edit
        and not _is_special_year(row["year_id"])
        and not is_placeholder
        and row["is_placeholder"]
        and (
            limit_err := _regular_submission_limit_error(
                cursor, user_id, row["year_id"], exclude_song_id=id
            )
        )
    ):
        return limit_err

    # Admin-only media fields are preserved for non-admins because their form
    # doesn't expose them.
    if permissions.can_edit:
        poster_link = text["poster_link"]
        vtt_link = text["vtt_link"]
    else:
        poster_link = row["poster_link"]
        vtt_link = row["vtt_link"]

    title_language_id, native_language_id = _resolve_language_ids(
        language_ids,
        is_translation,
        does_match,
        text["native_title"],
    )

    # Artist and title distinguish live songs from deletion sentinels.
    for field, label in REQUIRED_IDENTITY_FIELDS.items():
        if not text.get(field):
            return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")

    # ── Validation (non-admins) ──────────────────────────────────
    if not permissions.can_view_restricted:
        for field, label in REQUIRED_FIELDS.items():
            if not text.get(field):
                return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")
        if text["snippet_start"] and text["snippet_end"]:
            s = parse_seconds(text["snippet_start"])
            e = parse_seconds(text["snippet_end"])
            if s is not None and e is not None and (e - s) > MAX_SNIPPET_DURATION:
                return err(
                    ErrorID.BAD_REQUEST,
                    f"Snippet duration ({e - s}s) exceeds maximum ({MAX_SNIPPET_DURATION}s)",
                )
    if message := _snippet_duration_error(
        text,
        "snippet2_start",
        "snippet2_end",
        MAX_SNIPPET2_DURATION,
        "Snippet 2",
    ):
        return err(ErrorID.BAD_REQUEST, message)

    # ── Submitter override (admins only) ─────────────────────────
    submitter_id = user_id if is_claim else row["submitter_id"]
    if "submitter_id" in data and permissions.can_edit:
        raw = data["submitter_id"]
        if raw is None:
            submitter_id = None
        else:
            try:
                submitter_id = int(raw)
            except (ValueError, TypeError):
                return err(ErrorID.BAD_REQUEST, "submitter_id must be an integer or null")

    # ── Execute ──────────────────────────────────────────────────
    language_set_id = _get_or_create_language_set(cursor, language_ids)
    genre_set_id = _get_or_create_genre_set(cursor, subgenre_ids or [])
    key_signature_set_id = _get_or_create_key_signature_set(
        cursor, key_signatures or []
    )
    time_signature_set_id = _get_or_create_time_signature_set(
        cursor, time_signatures or []
    )
    current_credits = fetch_artist_credits(cursor, row["artist_credit_set_id"])
    if _same_artist_credits(artist_credits, current_credits):
        artist_credit_set_id = row["artist_credit_set_id"]
    else:
        artist_credit_set_id = create_artist_credit_set(cursor, artist_credits)

    revision_changes = {
        "title": text["title"],
        "native_title": text["native_title"],
        "artist_credit_set_id": artist_credit_set_id,
        "title_language_id": title_language_id,
        "native_language_id": native_language_id,
        "language_set_id": language_set_id,
        "genre_set_id": genre_set_id,
        "key_signature_set_id": key_signature_set_id,
        "time_signature_set_id": time_signature_set_id,
        "video_link": text["video_link"],
        "duration": duration_for_link(
            text["video_link"], row["video_link"], row["duration"]
        ),
        "poster_link": poster_link,
        "vtt_link": vtt_link,
        "snippet_start": parse_seconds(text["snippet_start"]),
        "snippet_end": parse_seconds(text["snippet_end"]),
        "snippet2_start": parse_seconds(text["snippet2_start"]),
        "snippet2_end": parse_seconds(text["snippet2_end"]),
        "translated_lyrics": text["translated_lyrics"],
        "romanized_lyrics": text["romanized_lyrics"],
        "native_lyrics": text["native_lyrics"],
        "notes": text["notes"],
        "sources": text["sources"],
        "submitter_id": submitter_id,
    }
    revision_changes = {
        field: value
        for field, value in revision_changes.items()
        if row[field] != value
    }
    entry_code_changed = entry_code_supplied and entry_code != row["entry_code"]
    if entry_code_changed:
        cursor.execute("UPDATE song SET entry_code = %s WHERE id = %s", (entry_code, id))
    if revision_changes or entry_code_changed:
        create_song_revision(cursor, id, revision_changes, changed_by=user_id)
    try:
        status_change = set_song_status(
            cursor, id, changed_by=user_id, is_placeholder=is_placeholder
        )
    except NonPlaceholderLimitError as exc:
        db.rollback()
        return err(ErrorID.FORBIDDEN, str(exc))

    if is_claim:
        _remove_submitter_spot_watches(
            cursor,
            submitter_id,
            row["year_id"],
            row["country_id"],
        )

    notifications = []
    if status_change is not None and not row["is_placeholder"] and is_placeholder:
        notifications = create_spot_watch_notifications(cursor, id, "placeholder")

    db.commit()
    for notification in notifications:
        notify_new_message(*notification)

    updated = _fetch_song(cursor, id)
    assert updated is not None  # existence verified at the top of the handler
    if is_claim:
        notify_placeholder_claim(row, updated, username)
    langs = _fetch_song_languages(cursor, id)
    ks = _fetch_song_key_signatures(cursor, id)
    ts = _fetch_song_time_signatures(cursor, id)
    sg = _fetch_song_subgenres(cursor, id)
    artists = _fetch_song_artists(cursor, id)
    return resp(_song_row_to_json(updated, langs, ks, ts, sg, artists))


# ── PATCH /api/song/<id> ─────────────────────────────────────────────


@bp.patch("/<int:id>")
@require_api_auth
def update_song(id: int, auth: tuple):
    user_id, _username, permissions = auth

    data, _is_form = _get_request_data()
    if not data:
        return err(ErrorID.BAD_REQUEST, "Request body must be JSON or form-encoded")

    db = get_db()
    cursor = db.cursor()

    row = _fetch_song(cursor, id)
    if not row:
        return err(ErrorID.NOT_FOUND, f"Song {id} not found")

    entry_code, entry_code_error = _parse_entry_code(data, permissions)
    if entry_code_error:
        return entry_code_error
    entry_code_supplied = "entry_code" in data

    # ── Permission check ─────────────────────────────────────────
    if (
        not permissions.can_edit
        and row["submitter_id"] != user_id
        and not _can_edit_national_final_song(cursor, id, user_id)
    ):
        return err(ErrorID.FORBIDDEN, "You can only edit your own submissions")

    artist_credits = None
    if "artists" in data or "artist" in data:
        artist_credits, artist_error = _parse_request_artists(data)
        if artist_error:
            return err(ErrorID.BAD_REQUEST, artist_error)
        assert artist_credits is not None

    # ── Build SET clause from provided fields ────────────────────
    # Column names are only ever drawn from fixed allowlists, but routing
    # them through sql.Identifier makes the safety explicit and satisfies
    # the typechecker (sql.SQL() requires LiteralString).
    sets: list[sql.Composable] = []
    params: list = []

    def _assign(col: str) -> sql.Composable:
        return sql.SQL("{} = %s").format(sql.Identifier(col))

    for field in MUTABLE_TEXT_FIELDS:
        if field in data and field != "artist":
            if field in ("snippet_start", "snippet_end", "snippet2_start", "snippet2_end"):
                val = _normalize_text(data[field])
                sets.append(_assign(field))
                params.append(parse_seconds(val))
            else:
                sets.append(_assign(field))
                params.append(_normalize_text(data[field]))

    if "video_link" in data:
        sets.append(_assign("duration"))
        params.append(
            duration_for_link(
                _normalize_text(data["video_link"]), row["video_link"], row["duration"]
            )
        )

    # ── Language-derived fields ───────────────────────────────────
    language_ids = None
    if "languages" in data:
        language_ids, lang_errors = _parse_languages(data)
        if lang_errors:
            return err(ErrorID.BAD_REQUEST, "; ".join(lang_errors))

    # ── Key signatures ───────────────────────────────────────────
    key_signatures, ks_errors = _parse_key_signatures(data)
    if ks_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(ks_errors))

    # ── Time signatures ──────────────────────────────────────────
    time_signatures, ts_errors = _parse_time_signatures(data)
    if ts_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(ts_errors))

    # ── Subgenres ────────────────────────────────────────────────
    subgenre_ids, sg_errors = _parse_subgenres(data)
    if sg_errors:
        return err(ErrorID.BAD_REQUEST, "; ".join(sg_errors))

    # Recalculate language IDs if languages or relevant flags changed
    if language_ids is not None or "is_translation" in data or "does_match" in data:
        cur_langs = (
            language_ids
            if language_ids is not None
            else [r["id"] for r in _fetch_song_languages(cursor, id)]
        )
        is_translation = bool(data.get("is_translation", False))
        does_match = bool(data.get("does_match", False))
        # For native_title, use the incoming value if provided, else the existing one
        if "native_title" in data:
            native_title = _normalize_text(data["native_title"])
        else:
            native_title = row["native_title"]
        title_language_id, native_language_id = _resolve_language_ids(
            cur_langs,
            is_translation,
            does_match,
            native_title,
        )
        sets.append(_assign("title_language_id"))
        params.append(title_language_id)
        sets.append(_assign("native_language_id"))
        params.append(native_language_id)

    # ── Submitter override (admins) ──────────────────────────────
    if "submitter_id" in data and permissions.can_edit:
        raw = data["submitter_id"]
        if raw is None:
            sets.append(_assign("submitter_id"))
            params.append(None)
        else:
            try:
                sets.append(_assign("submitter_id"))
                params.append(int(raw))
            except (ValueError, TypeError):
                return err(ErrorID.BAD_REQUEST, "submitter_id must be an integer or null")

    if (
        not sets
        and language_ids is None
        and key_signatures is None
        and time_signatures is None
        and subgenre_ids is None
        and artist_credits is None
        and "is_placeholder" not in data
        and not entry_code_supplied
    ):
        return err(ErrorID.BAD_REQUEST, "No fields to update")

    merged = dict(row)
    for field in MUTABLE_TEXT_FIELDS:
        if field in data:
            merged[field] = _normalize_text(data[field])
    if artist_credits is not None:
        merged["artist"] = render_artist_credits(artist_credits)

    # Artist and title distinguish live songs from deletion sentinels.
    for field, label in REQUIRED_IDENTITY_FIELDS.items():
        if not merged.get(field):
            return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")

    # ── Validation (non-admins) ──────────────────────────────────
    if not permissions.can_view_restricted:
        # Merge current values with incoming changes to validate the final state
        for field, label in REQUIRED_FIELDS.items():
            if not merged.get(field):
                return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")

        ss = merged.get("snippet_start")
        se = merged.get("snippet_end")
        if ss and se:
            s = ss if isinstance(ss, int) else parse_seconds(ss)
            e = se if isinstance(se, int) else parse_seconds(se)
            if s is not None and e is not None and (e - s) > MAX_SNIPPET_DURATION:
                return err(
                    ErrorID.BAD_REQUEST,
                    f"Snippet duration ({e - s}s) exceeds maximum ({MAX_SNIPPET_DURATION}s)",
                )
    if message := _snippet_duration_error(
        merged,
        "snippet2_start",
        "snippet2_end",
        MAX_SNIPPET2_DURATION,
        "Snippet 2",
    ):
        return err(ErrorID.BAD_REQUEST, message)

    if (
        not permissions.can_edit
        and not _is_special_year(row["year_id"])
        and row["is_placeholder"]
        and data.get("is_placeholder") is False
        and (
            limit_err := _regular_submission_limit_error(
                cursor, user_id, row["year_id"], exclude_song_id=id
            )
        )
    ):
        return limit_err

    # ── Execute ──────────────────────────────────────────────────
    changes = {}
    if sets:
        for field in MUTABLE_TEXT_FIELDS:
            if field in data and field != "artist":
                value = _normalize_text(data[field])
                if field in (
                    "snippet_start", "snippet_end", "snippet2_start", "snippet2_end"
                ):
                    value = parse_seconds(value)
                changes[field] = value
        if "video_link" in data:
            changes["duration"] = duration_for_link(
                changes["video_link"], row["video_link"], row["duration"]
            )
        if language_ids is not None or "is_translation" in data or "does_match" in data:
            changes["title_language_id"] = title_language_id
            changes["native_language_id"] = native_language_id
        if language_ids is not None:
            changes["language_set_id"] = _get_or_create_language_set(
                cursor, language_ids
            )
        if "submitter_id" in data and permissions.can_edit:
            raw_submitter = data["submitter_id"]
            changes["submitter_id"] = (
                None if raw_submitter is None else int(raw_submitter)
            )
    if key_signatures is not None:
        changes["key_signature_set_id"] = _get_or_create_key_signature_set(
            cursor, key_signatures
        )
    if time_signatures is not None:
        changes["time_signature_set_id"] = _get_or_create_time_signature_set(
            cursor, time_signatures
        )
    if subgenre_ids is not None:
        changes["genre_set_id"] = _get_or_create_genre_set(cursor, subgenre_ids)
    if artist_credits is not None:
        current_credits = fetch_artist_credits(cursor, row["artist_credit_set_id"])
        if not _same_artist_credits(artist_credits, current_credits):
            changes["artist_credit_set_id"] = create_artist_credit_set(
                cursor, artist_credits
            )
    changes = {field: value for field, value in changes.items() if row[field] != value}
    entry_code_changed = entry_code_supplied and entry_code != row["entry_code"]
    if entry_code_changed:
        cursor.execute("UPDATE song SET entry_code = %s WHERE id = %s", (entry_code, id))
    if changes or entry_code_changed:
        create_song_revision(cursor, id, changes, changed_by=user_id)

    if "is_placeholder" in data:
        try:
            status_change = set_song_status(
                cursor,
                id,
                changed_by=user_id,
                is_placeholder=bool(data["is_placeholder"]),
            )
        except NonPlaceholderLimitError as exc:
            db.rollback()
            return err(ErrorID.FORBIDDEN, str(exc))
    else:
        status_change = None

    notifications = []
    if (
        status_change is not None
        and not row["is_placeholder"]
        and bool(data.get("is_placeholder"))
    ):
        notifications = create_spot_watch_notifications(cursor, id, "placeholder")

    db.commit()
    for notification in notifications:
        notify_new_message(*notification)

    updated = _fetch_song(cursor, id)
    assert updated is not None  # existence verified at the top of the handler
    langs = _fetch_song_languages(cursor, id)
    ks = _fetch_song_key_signatures(cursor, id)
    ts = _fetch_song_time_signatures(cursor, id)
    sg = _fetch_song_subgenres(cursor, id)
    artists = _fetch_song_artists(cursor, id)
    return resp(_song_row_to_json(updated, langs, ks, ts, sg, artists))


# ── DELETE /api/song/<id> ─────────────────────────────────────────────


@bp.delete("/<int:id>")
@require_api_auth
def delete_song(id: int, auth: tuple):
    user_id, _username, permissions = auth

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT song.id, song.submitter_id, song.is_placeholder,
               year.submissions_open
        FROM current_song AS song
        JOIN year ON song.year_id = year.id
        WHERE song.id = %s
    """,
        (id,),
    )
    row = cursor.fetchone()

    if not row:
        return "", 204

    if not row["submissions_open"] and not permissions.can_edit:
        return err(ErrorID.FORBIDDEN, "Cannot delete a song for a current or past year")

    if not permissions.can_edit and row["submitter_id"] != user_id:
        return err(ErrorID.FORBIDDEN, "You can only delete your own submissions")

    withdraw_song(cursor, id, changed_by=user_id)
    notifications = create_spot_watch_notifications(cursor, id, "deleted")
    db.commit()
    for notification in notifications:
        notify_new_message(*notification)

    return "", 204
