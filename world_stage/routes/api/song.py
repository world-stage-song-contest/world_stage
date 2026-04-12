import unicodedata
from flask import Blueprint, make_response, redirect, request, url_for

from world_stage.db import fetchone, get_db
from world_stage.utils import (
    ErrorID, err, format_seconds, get_api_auth, get_user_permissions,
    parse_seconds, resolve_country_code, resp,
)

bp = Blueprint('song', __name__, url_prefix='/song')

# ── Constants ────────────────────────────────────────────────────────
ENGLISH_LANG_ID = 20
MAX_SNIPPET_DURATION = 20
MAX_USER_SUBMISSIONS = 2
MAX_YEAR_SUBMISSIONS = 73

MUTABLE_TEXT_FIELDS = (
    'title', 'native_title', 'artist', 'video_link', 'poster_link',
    'snippet_start', 'snippet_end', 'translated_lyrics',
    'romanized_lyrics', 'native_lyrics', 'notes', 'sources',
)

REQUIRED_FIELDS = {
    'artist': 'Artist',
    'title': 'Title',
    'sources': 'Sources',
}


# ── Helpers ──────────────────────────────────────────────────────────

def _normalize_text(value) -> str | None:
    if value is None or value == '':
        return None
    value = str(value).strip()
    value = unicodedata.normalize('NFC', value)
    value = value.replace('\r', '')
    return None if value == '' else value


def _form_bool(value: str | None) -> bool:
    """Interpret a form-encoded boolean (on/off, true/false, 1/0)."""
    return value in ('on', 'true', '1', 'yes')


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
    ct = request.content_type or ''

    # ── JSON ─────────────────────────────────────────────────────
    if 'json' in ct:
        return request.get_json(silent=True), False

    # ── Form ─────────────────────────────────────────────────────
    if 'form' in ct:
        form = request.form.to_dict()
        if not form:
            return None, True

        data: dict = {}

        # Languages: language=id&language=id&…
        raw_langs = request.form.getlist('language')
        if raw_langs:
            lang_ids: list[int] = []
            for val in raw_langs:
                try:
                    lang_ids.append(int(val))
                except (ValueError, TypeError):
                    pass
            if lang_ids:
                data['languages'] = lang_ids

        # Booleans
        for field in ('is_placeholder', 'admin_approved', 'is_translation', 'does_match'):
            if field in form:
                data[field] = _form_bool(form.get(field))

        # Scalars
        for field in ('year', 'country', 'submitter_id'):
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
    raw = data.get('languages', [])
    if not isinstance(raw, list):
        return [], ['languages must be a list of language IDs']
    try:
        ids = [int(x) for x in raw]
    except (ValueError, TypeError):
        return [], ['Each language ID must be an integer']
    if not ids:
        errors.append('At least one language must be provided')
    return ids, errors


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


def _song_row_to_json(row: dict, languages: list[dict]) -> dict:
    """Turn a DB row + language list into the API response body."""
    return {
        'id': row['id'],
        'year': row['year_id'],
        'country_id': row['country_id'],
        'country_name': row['country_name'],
        'title': row['title'],
        'native_title': row['native_title'],
        'artist': row['artist'],
        'is_placeholder': row['is_placeholder'],
        'video_link': row['video_link'],
        'poster_link': row['poster_link'],
        'snippet_start': format_seconds(row['snippet_start']) if row['snippet_start'] else None,
        'snippet_end': format_seconds(row['snippet_end']) if row['snippet_end'] else None,
        'translated_lyrics': row['translated_lyrics'],
        'romanized_lyrics': row['romanized_lyrics'],
        'native_lyrics': row['native_lyrics'],
        'notes': row['notes'],
        'sources': row['sources'],
        'admin_approved': row['admin_approved'],
        'submitter_id': row['submitter_id'],
        'submitter_name': row.get('username'),
        'languages': languages,
    }


def _fetch_song(cursor, song_id: int) -> dict | None:
    cursor.execute('''
        SELECT song.id, song.year_id, song.country_id, country.name AS country_name,
               song.title, song.native_title, song.artist, song.is_placeholder,
               song.title_language_id, song.native_language_id,
               song.video_link, song.poster_link,
               song.snippet_start, song.snippet_end,
               song.translated_lyrics, song.romanized_lyrics, song.native_lyrics,
               song.notes, song.sources, song.admin_approved,
               song.submitter_id, account.username
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT JOIN account ON song.submitter_id = account.id
        WHERE song.id = %s
    ''', (song_id,))
    return cursor.fetchone()


def _fetch_song_languages(cursor, song_id: int) -> list[dict]:
    cursor.execute('''
        SELECT language.id, language.name
        FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_id = %s
        ORDER BY priority
    ''', (song_id,))
    return [{'id': r['id'], 'name': r['name']} for r in cursor.fetchall()]


def _set_audit_user(cursor, user_id: int | None) -> None:
    cursor.execute(
        "SELECT set_config('app.current_user_id', %s, false)",
        (str(user_id) if user_id else '',),
    )


def _validate_country(country_code: str) -> tuple[str | None, tuple | None]:
    """Resolve & validate a country code. Returns (resolved_cc, error_response)."""
    cc = resolve_country_code(country_code.upper())
    if not cc:
        return None, err(ErrorID.NOT_FOUND, f"Country '{country_code}' not found")
    return cc, None


def _validate_year(cursor, year: int) -> tuple | None:
    """Return an error response if the year doesn't exist, else None."""
    cursor.execute('SELECT id, closed FROM year WHERE id = %s', (year,))
    row = cursor.fetchone()
    if not row:
        return err(ErrorID.NOT_FOUND, f"Year {year} not found")
    return None


# ── GET /api/song/<id> ───────────────────────────────────────────────

@bp.get('/<int:id>')
def get_song(id: int):
    db = get_db()
    cursor = db.cursor()

    row = _fetch_song(cursor, id)
    if not row:
        return err(ErrorID.NOT_FOUND, f"Song {id} not found")

    languages = _fetch_song_languages(cursor, id)
    return resp(_song_row_to_json(row, languages))


# ── GET /api/song/<cc>/<year> ─────────────────────────────────────────

@bp.get('/<cc>/<int:year>')
def get_song_by_country(cc: str, year: int):
    canonical = resolve_country_code(cc.upper())
    if canonical and canonical.lower() != cc.lower():
        return redirect(url_for('api.song.get_song_by_country', cc=canonical.lower(), year=year), 301)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.year_id, song.country_id, country.name AS country_name,
               song.title, song.native_title, song.artist, song.is_placeholder,
               song.title_language_id, song.native_language_id,
               song.video_link, song.poster_link,
               song.snippet_start, song.snippet_end,
               song.translated_lyrics, song.romanized_lyrics, song.native_lyrics,
               song.notes, song.sources, song.admin_approved,
               song.submitter_id, account.username
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT JOIN account ON song.submitter_id = account.id
        WHERE (song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id = %(year)s
    ''', {'cc': cc.upper(), 'year': year})
    row = cursor.fetchone()

    if not row:
        return err(ErrorID.NOT_FOUND, f"No song found for {cc} in {year}")

    languages = _fetch_song_languages(cursor, row['id'])
    return resp(_song_row_to_json(row, languages))


# ── POST /api/song ───────────────────────────────────────────────────

@bp.post('')
def create_song():
    auth = get_api_auth()
    if not auth:
        return err(ErrorID.UNAUTHORIZED, "Authentication required")
    user_id, _username, permissions = auth

    data, _is_form = _get_request_data()
    if not data:
        return err(ErrorID.BAD_REQUEST, "Request body must be JSON or form-encoded")

    # ── Required fields ──────────────────────────────────────────
    year = data.get('year')
    country_code = data.get('country')
    if not year or not country_code:
        return err(ErrorID.BAD_REQUEST, "year and country are required")

    try:
        year = int(year)
    except (ValueError, TypeError):
        return err(ErrorID.BAD_REQUEST, "year must be an integer")

    db = get_db()
    cursor = db.cursor()

    year_err = _validate_year(cursor, year)
    if year_err:
        return year_err

    cc, cc_err = _validate_country(country_code)
    if cc_err:
        return cc_err

    # ── Check for duplicates ─────────────────────────────────────
    cursor.execute(
        'SELECT id FROM song WHERE year_id = %s AND country_id = %s',
        (year, cc),
    )
    if cursor.fetchone():
        return err(ErrorID.CONFLICT, f"A song already exists for {cc} in {year}. Use PATCH to update it.")

    # ── Submission limits (non-admins) ───────────────────────────
    if not permissions.can_edit:
        cursor.execute(
            'SELECT COUNT(*) AS c FROM song WHERE submitter_id = %s AND year_id = %s AND NOT is_placeholder',
            (user_id, year),
        )
        if fetchone(cursor)['c'] >= MAX_USER_SUBMISSIONS:
            return err(ErrorID.FORBIDDEN, f"You may submit at most {MAX_USER_SUBMISSIONS} songs per year")

        cursor.execute(
            'SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder',
            (year,),
        )
        if fetchone(cursor)['c'] >= MAX_YEAR_SUBMISSIONS:
            return err(ErrorID.FORBIDDEN, f"This year already has the maximum number of entries ({MAX_YEAR_SUBMISSIONS})")

    # ── Parse body ───────────────────────────────────────────────
    language_ids, lang_errors = _parse_languages(data)
    if lang_errors:
        return err(ErrorID.BAD_REQUEST, '; '.join(lang_errors))

    text = {k: _normalize_text(data.get(k)) for k in MUTABLE_TEXT_FIELDS}

    is_placeholder = bool(data.get('is_placeholder', False))
    is_translation = bool(data.get('is_translation', False))
    does_match = bool(data.get('does_match', False))
    admin_approved = bool(data.get('admin_approved', False)) and permissions.can_edit

    title_language_id, native_language_id = _resolve_language_ids(
        language_ids, is_translation, does_match, text['native_title'],
    )

    # ── Validation (non-admins) ──────────────────────────────────
    if not permissions.can_view_restricted:
        for field, label in REQUIRED_FIELDS.items():
            if not text.get(field):
                return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")
        if text['snippet_start'] and text['snippet_end']:
            s = parse_seconds(text['snippet_start'])
            e = parse_seconds(text['snippet_end'])
            if s is not None and e is not None and (e - s) > MAX_SNIPPET_DURATION:
                return err(ErrorID.BAD_REQUEST,
                           f"Snippet duration ({e - s}s) exceeds maximum ({MAX_SNIPPET_DURATION}s)")

    # ── Submitter override (admins only) ─────────────────────────
    submitter_id = user_id
    if permissions.can_edit and 'submitter_id' in data:
        raw = data['submitter_id']
        if raw is None:
            submitter_id = None
        else:
            try:
                submitter_id = int(raw)
            except (ValueError, TypeError):
                return err(ErrorID.BAD_REQUEST, "submitter_id must be an integer or null")

    # ── Insert ───────────────────────────────────────────────────
    _set_audit_user(cursor, user_id)

    cursor.execute('''
        INSERT INTO song (
            year_id, country_id, title, native_title, artist, is_placeholder,
            title_language_id, native_language_id, video_link, poster_link,
            snippet_start, snippet_end, translated_lyrics,
            romanized_lyrics, native_lyrics, submitter_id,
            notes, sources, admin_approved, modified_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING id
    ''', (
        year, cc, text['title'], text['native_title'], text['artist'],
        is_placeholder, title_language_id, native_language_id,
        text['video_link'], text['poster_link'],
        parse_seconds(text['snippet_start']), parse_seconds(text['snippet_end']),
        text['translated_lyrics'], text['romanized_lyrics'], text['native_lyrics'],
        submitter_id, text['notes'], text['sources'], admin_approved,
    ))
    song_id = fetchone(cursor)['id']

    for i, lang_id in enumerate(language_ids):
        cursor.execute(
            'INSERT INTO song_language (song_id, language_id, priority) VALUES (%s, %s, %s)',
            (song_id, lang_id, i),
        )

    db.commit()

    row = _fetch_song(cursor, song_id)
    languages = _fetch_song_languages(cursor, song_id)
    body, code = resp(_song_row_to_json(row, languages), 201)
    response = make_response(body, code)
    response.headers['Location'] = url_for('api.song.get_song', id=song_id)
    return response


# ── PATCH /api/song/<id> ─────────────────────────────────────────────

@bp.patch('/<int:id>')
def update_song(id: int):
    auth = get_api_auth()
    if not auth:
        return err(ErrorID.UNAUTHORIZED, "Authentication required")
    user_id, _username, permissions = auth

    data, _is_form = _get_request_data()
    if not data:
        return err(ErrorID.BAD_REQUEST, "Request body must be JSON or form-encoded")

    db = get_db()
    cursor = db.cursor()

    row = _fetch_song(cursor, id)
    if not row:
        return err(ErrorID.NOT_FOUND, f"Song {id} not found")

    # ── Permission check ─────────────────────────────────────────
    if not permissions.can_edit and row['submitter_id'] != user_id:
        return err(ErrorID.FORBIDDEN, "You can only edit your own submissions")

    # ── Build SET clause from provided fields ────────────────────
    sets: list[str] = []
    params: list = []

    for field in MUTABLE_TEXT_FIELDS:
        if field in data:
            if field in ('snippet_start', 'snippet_end'):
                val = _normalize_text(data[field])
                sets.append(f'{field} = %s')
                params.append(parse_seconds(val))
            else:
                sets.append(f'{field} = %s')
                params.append(_normalize_text(data[field]))

    if 'is_placeholder' in data:
        sets.append('is_placeholder = %s')
        params.append(bool(data['is_placeholder']))

    if 'admin_approved' in data and permissions.can_edit:
        sets.append('admin_approved = %s')
        params.append(bool(data['admin_approved']))

    # ── Language-derived fields ───────────────────────────────────
    language_ids = None
    if 'languages' in data:
        language_ids, lang_errors = _parse_languages(data)
        if lang_errors:
            return err(ErrorID.BAD_REQUEST, '; '.join(lang_errors))

    # Recalculate language IDs if languages or relevant flags changed
    if language_ids is not None or 'is_translation' in data or 'does_match' in data:
        cur_langs = language_ids if language_ids is not None else [
            r['id'] for r in _fetch_song_languages(cursor, id)
        ]
        is_translation = bool(data.get('is_translation', False))
        does_match = bool(data.get('does_match', False))
        # For native_title, use the incoming value if provided, else the existing one
        native_title = _normalize_text(data['native_title']) if 'native_title' in data else row['native_title']
        title_language_id, native_language_id = _resolve_language_ids(
            cur_langs, is_translation, does_match, native_title,
        )
        sets.append('title_language_id = %s')
        params.append(title_language_id)
        sets.append('native_language_id = %s')
        params.append(native_language_id)

    # ── Submitter override (admins) ──────────────────────────────
    if 'submitter_id' in data and permissions.can_edit:
        raw = data['submitter_id']
        if raw is None:
            sets.append('submitter_id = %s')
            params.append(None)
        else:
            try:
                sets.append('submitter_id = %s')
                params.append(int(raw))
            except (ValueError, TypeError):
                return err(ErrorID.BAD_REQUEST, "submitter_id must be an integer or null")

    if not sets and language_ids is None:
        return err(ErrorID.BAD_REQUEST, "No fields to update")

    # ── Validation (non-admins) ──────────────────────────────────
    if not permissions.can_view_restricted:
        # Merge current values with incoming changes to validate the final state
        merged = dict(row)
        for field in MUTABLE_TEXT_FIELDS:
            if field in data:
                merged[field] = _normalize_text(data[field])
        for field, label in REQUIRED_FIELDS.items():
            if not merged.get(field):
                return err(ErrorID.BAD_REQUEST, f"Missing required field: {label}")

        ss = merged.get('snippet_start')
        se = merged.get('snippet_end')
        if ss and se:
            s = ss if isinstance(ss, int) else parse_seconds(ss)
            e = se if isinstance(se, int) else parse_seconds(se)
            if s is not None and e is not None and (e - s) > MAX_SNIPPET_DURATION:
                return err(ErrorID.BAD_REQUEST,
                           f"Snippet duration ({e - s}s) exceeds maximum ({MAX_SNIPPET_DURATION}s)")

    # ── Execute ──────────────────────────────────────────────────
    _set_audit_user(cursor, user_id)

    if sets:
        sets.append('modified_at = CURRENT_TIMESTAMP')
        params.append(id)
        cursor.execute(
            f'UPDATE song SET {", ".join(sets)} WHERE id = %s',
            params,
        )

    if language_ids is not None:
        cursor.execute('DELETE FROM song_language WHERE song_id = %s', (id,))
        for i, lang_id in enumerate(language_ids):
            cursor.execute(
                'INSERT INTO song_language (song_id, language_id, priority) VALUES (%s, %s, %s)',
                (id, lang_id, i),
            )

    db.commit()

    updated = _fetch_song(cursor, id)
    langs = _fetch_song_languages(cursor, id)
    return resp(_song_row_to_json(updated, langs))


# ── DELETE /api/song/<id> ─────────────────────────────────────────────

@bp.delete('/<int:id>')
def delete_song(id: int):
    auth = get_api_auth()
    if not auth:
        return err(ErrorID.UNAUTHORIZED, "Authentication required")
    user_id, _username, permissions = auth

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.submitter_id, year.closed
        FROM song
        JOIN year ON song.year_id = year.id
        WHERE song.id = %s
    ''', (id,))
    row = cursor.fetchone()

    if not row:
        return '', 204

    if row['closed'] != 0 and not permissions.can_edit:
        return err(ErrorID.FORBIDDEN, "Cannot delete a song for a current or past year")

    if not permissions.can_edit and row['submitter_id'] != user_id:
        return err(ErrorID.FORBIDDEN, "You can only delete your own submissions")

    _set_audit_user(cursor, user_id)

    cursor.execute('DELETE FROM song_language WHERE song_id = %s', (id,))
    cursor.execute('DELETE FROM song WHERE id = %s', (id,))
    db.commit()

    return '', 204
