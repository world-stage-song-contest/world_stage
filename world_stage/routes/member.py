from dataclasses import dataclass
import unicodedata
from flask import Blueprint, redirect, request, url_for
from typing import Any, Optional, NamedTuple

from ..db import get_db
from ..utils import (
    UserPermissions,
    get_user_id_from_session, format_seconds, get_user_permissions,
    get_user_role_from_session, get_years, parse_seconds, render_template
)

bp = Blueprint('member', __name__, url_prefix='/member')

# Constants
ENGLISH_LANG_ID = 20
MAX_SNIPPET_DURATION = 20
MAX_USER_SUBMISSIONS = 2
MAX_YEAR_SUBMISSIONS = 73

class ValidationError(Exception):
    """Raised when form validation fails"""
    pass

class Result(NamedTuple):
    success: bool
    message: str
    error: str | None = None

@dataclass
class SongData:
    year: int
    country: str
    title: str
    native_title: str | None
    artist: str
    is_placeholder: bool
    title_language_id: int | None
    native_language_id: int | None
    video_link: str | None
    poster_link: str | None
    snippet_start: str | None
    snippet_end: str | None
    translated_lyrics: str | None
    romanized_lyrics: str | None
    native_lyrics: str | None
    notes: str | None
    languages: list[dict]
    sources: str
    admin_approved: bool
    user_id: int | None = None

    def __post_init__(self):
        """Calculate derived fields after initialization"""
        if self.languages and isinstance(self.languages[0], dict):
            first_language_id = self.languages[0]['id']
            self.is_translation = first_language_id != self.title_language_id
            self.does_match = first_language_id == self.native_language_id

    def as_dict(self) -> dict[str, Any]:
        return {attr: getattr(self, attr) for attr in self.__dict__}

@dataclass
class FormData:
    year: int
    country: str
    title: str | None
    native_title: str | None
    artist: str | None
    is_placeholder: bool
    title_language_id: int | None
    native_language_id: int | None
    video_link: str | None
    poster_link: str | None
    snippet_start: str | None
    snippet_end: str | None
    translated_lyrics: str | None
    romanized_lyrics: str | None
    native_lyrics: str | None
    notes: str | None
    sources: str | None
    admin_approved: bool
    languages: list[int]
    is_translation: bool
    does_match: bool

    @classmethod
    def from_request(cls, form: dict[str, str]) -> tuple['FormData', list[str]]:
        """Parse form data and return (data, validation_errors)"""
        errors = []

        # Parse languages
        languages = []
        invalid_languages = []
        for key, value in form.items():
            if key.startswith('language'):
                try:
                    n = int(key.removeprefix('language'))
                    languages.append((n, int(value)))
                except ValueError:
                    invalid_languages.append(key)

        languages.sort(key=lambda x: x[0])
        language_ids = [x[1] for x in languages]

        if not language_ids:
            errors.append('At least one language must be selected')

        # Parse boolean fields
        boolean_data = {
            'is_placeholder': form.get('is_placeholder', 'off') == 'on',
            'admin_approved': form.get('admin_approved', 'off') == 'on',
            'is_translation': form.get('is_translation', 'off') == 'on',
            'does_match': form.get('does_match', 'off') == 'on',
        }

        # Parse and normalize text fields
        text_data = {}
        for key in ['title', 'native_title', 'artist', 'video_link', 'poster_link',
                   'snippet_start', 'snippet_end', 'translated_lyrics',
                   'romanized_lyrics', 'native_lyrics', 'notes', 'sources']:
            value = form.get(key, '').strip()
            value = unicodedata.normalize('NFC', value)
            value = value.replace('\r', '')
            text_data[key] = None if value == '' else value

        # Parse numeric fields
        try:
            year = int(form.get('year', 0))
            country = form.get('country', 'XXX')
        except ValueError:
            errors.append('Invalid year format')
            year = 0
            country = 'XXX'

        # Determine language IDs based on logic
        native_language_id = None
        title_language_id = None

        if language_ids:
            if boolean_data['does_match'] or not text_data.get('native_title'):
                native_language_id = language_ids[0]

            if boolean_data['is_translation']:
                title_language_id = ENGLISH_LANG_ID
            else:
                title_language_id = native_language_id

        return cls(
            year=year,
            country=country,
            languages=language_ids,
            title_language_id=title_language_id,
            native_language_id=native_language_id,
            **text_data, # type: ignore
            **boolean_data # type: ignore
        ), errors

class SongValidator:
    REQUIRED_FIELDS = {
        'artist': "Artist",
        'title': "Latin title",
        'sources': "Sources",
    }

    def validate_submission(self, data: FormData, user_permissions: UserPermissions) -> list[str]:
        """Validate form data for submission"""
        errors = []

        if user_permissions.can_view_restricted:
            return []

        # Required field validation
        for field, display_name in self.REQUIRED_FIELDS.items():
            if not getattr(data, field):
                errors.append(f"Missing required field: {display_name}")

        # Snippet duration validation
        if data.snippet_start and data.snippet_end:
            start_seconds = parse_seconds(data.snippet_start)
            end_seconds = parse_seconds(data.snippet_end)
            if start_seconds is not None and end_seconds is not None:
                duration = end_seconds - start_seconds
                if duration > MAX_SNIPPET_DURATION:
                    errors.append(f"Snippet duration ({duration}s) exceeds maximum ({MAX_SNIPPET_DURATION}s)")

        return errors

class SongRepository:
    def __init__(self):
        self.db = get_db()
        self.cursor = self.db.cursor()

    def find_song(self, year: int, country: str) -> tuple[int, dict] | None:
        """Find existing song by year and country"""
        self.cursor.execute('''
            SELECT id, title, native_title, artist, is_placeholder,
                   title_language_id, native_language_id, video_link, poster_link
                   snippet_start, snippet_end, translated_lyrics,
                   romanized_lyrics, native_lyrics, notes, submitter_id,
                   sources, admin_approved
            FROM song
            WHERE year_id = %s AND country_id = %s
        ''', (year, country))
        song_data = self.cursor.fetchone()
        if song_data:
            return song_data.pop('id'), song_data
        else:
            return None

    def get_song_languages(self, song_id: int) -> list[dict]:
        """Get languages for a song"""
        self.cursor.execute('''
            SELECT language.id, name FROM song_language
            JOIN language ON song_language.language_id = language.id
            WHERE song_id = %s
            ORDER BY priority
        ''', (song_id,))
        return [{'id': row['id'], 'name': row['name']} for row in self.cursor.fetchall()]

    def can_delete_song(self, year: int, country: str) -> tuple[bool, str | None, int | None]:
        """Check if song can be deleted, return (can_delete, error_msg, submitter_id)"""
        self.cursor.execute('''
            SELECT song.id, submitter_id, closed FROM song
            JOIN year ON song.year_id = year.id
            WHERE year_id = %s AND country_id = %s
        ''', (year, country))

        result = self.cursor.fetchone()
        if not result:
            return False, 'Song not found', None

        if result['closed'] != 0:
            return False, "Can't delete a song for a current/past year", None

        return True, None, result['submitter_id']

    def delete_song(self, year: int, country: str) -> bool:
        """Delete song and associated data"""
        # Get song ID first
        self.cursor.execute('''
            SELECT id FROM song
            WHERE year_id = %s AND country_id = %s
        ''', (year, country))

        result = self.cursor.fetchone()
        if not result:
            return False

        song_id = result['id']

        # Delete in correct order (foreign key constraints)
        self.cursor.execute('DELETE FROM song_language WHERE song_id = %s', (song_id,))
        self.cursor.execute('DELETE FROM song WHERE id = %s', (song_id,))

        self.db.commit()
        return True

    def upsert_song(self, data: FormData, user_id: int | None) -> int:
        """Insert or update song, return song_id"""
        existing_song = self.find_song(data.year, data.country)

        if existing_song:
            song_id, _ = existing_song
            self.cursor.execute('''
                UPDATE song
                SET title = %s, native_title = %s, artist = %s, is_placeholder = %s,
                    title_language_id = %s, native_language_id = %s, video_link = %s, poster_link = %s,
                    snippet_start = %s, snippet_end = %s, translated_lyrics = %s,
                    romanized_lyrics = %s, native_lyrics = %s, submitter_id = %s,
                    notes = %s, sources = %s, admin_approved = %s,
                    modified_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (
                data.title, data.native_title, data.artist, data.is_placeholder,
                data.title_language_id, data.native_language_id, data.video_link, data.poster_link,
                parse_seconds(data.snippet_start), parse_seconds(data.snippet_end),
                data.translated_lyrics, data.romanized_lyrics, data.native_lyrics,
                user_id, data.notes, data.sources, data.admin_approved, song_id
            ))
        else:
            self.cursor.execute('''
                INSERT INTO song (
                    year_id, country_id, title, native_title, artist, is_placeholder,
                    title_language_id, native_language_id, video_link, poster_link
                    snippet_start, snippet_end, translated_lyrics,
                    romanized_lyrics, native_lyrics, submitter_id,
                    notes, sources, admin_approved, modified_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            ''', (
                data.year, data.country, data.title, data.native_title, data.artist,
                data.is_placeholder, data.title_language_id, data.native_language_id,
                data.video_link, data.poster_link, parse_seconds(data.snippet_start), parse_seconds(data.snippet_end),
                data.translated_lyrics, data.romanized_lyrics, data.native_lyrics,
                user_id, data.notes, data.sources, data.admin_approved
            ))
            song_id = self.cursor.fetchone()['id'] # type: ignore

        # Update languages
        self.cursor.execute('DELETE FROM song_language WHERE song_id = %s', (song_id,))
        for i, lang_id in enumerate(data.languages):
            self.cursor.execute('''
                INSERT INTO song_language (song_id, language_id, priority)
                VALUES (%s, %s, %s)
            ''', (song_id, lang_id, i))

        self.db.commit()
        return song_id

class SongService:
    def __init__(self):
        self.repo = SongRepository()
        self.validator = SongValidator()

    def delete_song(self, year: int, country: str, artist: str, title: str, user_id: int | None) -> Result:
        """Delete song with permission checking"""
        can_delete, error_msg, submitter_id = self.repo.can_delete_song(year, country)

        if not can_delete:
            return Result(False, "", error_msg)

        permissions = get_user_permissions(user_id)
        if not permissions.can_edit and submitter_id != user_id:
            return Result(False, "", "You are not the submitter")

        success = self.repo.delete_song(year, country)
        if success:
            return Result(True, f'The song "{artist} — {title}" has been deleted from {year}')
        else:
            return Result(False, "", "Failed to delete song")

    def submit_song(self, data: FormData, user_id: int | None) -> Result:
        """Submit song with validation"""
        permissions = get_user_permissions(user_id)
        validation_errors = self.validator.validate_submission(data, permissions)

        if validation_errors:
            return Result(False, "", "; ".join(validation_errors))

        try:
            song_id = self.repo.upsert_song(data, user_id)
            return Result(True, f'The song "{data.artist} — {data.title}" has been submitted for {data.year}')
        except Exception as e:
            return Result(False, "", f"Database error: {str(e)}")

def get_languages() -> list[dict]:
    """Get all available languages"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, name FROM language ORDER BY name')
    return cursor.fetchall()

def get_countries(year: int, user_id: int | None, all: bool = False) -> dict[str, list[dict]]:
    """Get countries available for submission"""
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT closed FROM year WHERE id = %s', (year,))
    year_result = cursor.fetchone()
    if not year_result:
        return {'own': [], 'placeholder': []}

    closed = year_result['closed']

    cursor.execute('SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder', (year,))
    year_count = cursor.fetchone()['c'] # type: ignore

    cursor.execute('''
        SELECT COUNT(*) AS c FROM song
        WHERE submitter_id = %s AND year_id = %s AND NOT is_placeholder
    ''', (user_id, year))
    user_count = cursor.fetchone()['c'] # type: ignore

    countries: dict[str, list[dict[str, Any]]] = {'own': [], 'placeholder': []}

    # Get user's own submissions
    cursor.execute('''
        SELECT country.name, country.id AS cc FROM song
        JOIN country ON song.country_id = country.id
        WHERE song.year_id = %s AND song.submitter_id = %s
        ORDER BY country.name
    ''', (year, user_id))
    countries['own'] = cursor.fetchall()

    if all:
        if closed:
            cursor.execute('''
                SELECT country.name, country.id AS cc FROM song
                JOIN country ON song.country_id = country.id
                WHERE song.year_id = %(year)s AND submitter_id <> %(user)s
                ORDER BY country.name
            ''', {'year': year, 'user': user_id})
        else:
            cursor.execute('''
                SELECT name, id AS cc FROM country
                WHERE available_from <= %(year)s AND available_until >= %(year)s
                      AND is_participating
                      AND id NOT IN (
                          SELECT country_id FROM song
                          WHERE year_id = %(year)s AND submitter_id = %(user)s
                      )
                ORDER BY name
            ''', {'year': year, 'user': user_id})
        countries['placeholder'] = cursor.fetchall()
    elif user_count < MAX_USER_SUBMISSIONS and not closed and year_count < MAX_YEAR_SUBMISSIONS:
        cursor.execute('''
            SELECT c.name, c.id AS cc
            FROM country AS c
            WHERE c.available_from <= %(year)s AND c.available_until >= %(year)s
              AND c.is_participating
              AND NOT EXISTS (
                SELECT 1
                FROM song AS s
                WHERE s.year_id = %(year)s
                AND s.country_id = c.id
                AND s.is_placeholder = FALSE
                AND s.submitter_id <> %(user)s
            )
            ORDER BY c.name
        ''', {'year': year, 'user': user_id})
        countries['placeholder'] = cursor.fetchall()

    return countries

def get_users() -> list[dict]:
    """Get all users for admin dropdown"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, username FROM account ORDER BY username')
    return cursor.fetchall()

def parse_user_assignment(form_data: dict, permissions, default_user_id: int) -> tuple[int | None, bool]:
    """Parse user assignment from form, return (user_id, set_claim)"""
    force_submitter = form_data.get('force_submitter')
    if force_submitter and permissions.can_edit:
        if force_submitter == 'none':
            return None, True
        else:
            return int(force_submitter.strip()), True
    return default_user_id, False

# Route handlers
@bp.get('/')
def index():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT account.username
        FROM session
        JOIN account ON session.user_id = account.id
        WHERE session_id = %s
    ''', (session_id,))

    result = cursor.fetchone()
    if not result:
        resp = redirect(url_for('session.login'))
        resp.delete_cookie('session')
        return resp

    return render_template('member/index.html', username=result['username'])

@bp.get('/submit')
def submit():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    year = request.args.get('year')
    country = request.args.get('country')
    permissions = get_user_role_from_session(session_id)

    return render_template('member/submit.html',
                         year=year, country=country, elevated=permissions.can_edit,
                         years=get_years(), languages=get_languages(),
                         countries={}, data={}, onLoad=True, users=get_users())

@bp.get('/submit/<int:year>')
def get_countries_for_year(year: int):
    session_data = get_user_id_from_session(request.cookies.get('session'))
    user_id = session_data[0] if session_data else None
    permissions = get_user_permissions(user_id)
    countries = get_countries(year, user_id, all=permissions.can_edit)
    return {'countries': countries}

@bp.get('/submit/<int:year>/<country>')
def get_country_data(year: int, country: str):
    session_data = get_user_id_from_session(request.cookies.get('session'))
    user_id = session_data[0] if session_data else None

    repo = SongRepository()
    song_info = repo.find_song(year, country)
    if not song_info:
        return {'error': 'Song not found'}, 404

    song_id, song = song_info

    submitter_id = song.pop('submitter_id') or 0
    is_placeholder = song.pop('is_placeholder')

    # Format time fields
    snippet_start = format_seconds(song.pop('snippet_start'))
    snippet_end = format_seconds(song.pop('snippet_end'))

    # Get languages
    languages = repo.get_song_languages(song_id)

    song_data = SongData(
        year=year,
        country=country,
        is_placeholder=bool(is_placeholder) and user_id is not None and user_id != submitter_id,
        snippet_start=snippet_start,
        snippet_end=snippet_end,
        languages=languages,
        user_id=submitter_id,
        **song
    )

    return song_data.as_dict()

@bp.post('/submit')
def submit_song_post():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    user_data = get_user_id_from_session(session_id)
    if not user_data:
        return redirect(url_for('session.login'))

    default_user_id = user_data[0]
    permissions = get_user_permissions(default_user_id)

    # Parse form data
    form_data, parse_errors = FormData.from_request(request.form)
    if parse_errors:
        return render_error_template(form_data, parse_errors[0], [])

    # Handle user assignment
    user_id, set_claim = parse_user_assignment(request.form.to_dict(), permissions, default_user_id)

    action = request.form['action']
    service = SongService()

    try:
        if action == 'delete':
            result = service.delete_song(form_data.year, form_data.country,
                                       form_data.artist or "", form_data.title or "", user_id)
        elif action == 'submit':
            validation_errors = service.validator.validate_submission(form_data, permissions)
            if validation_errors:
                return render_error_template(form_data, validation_errors[0], [])

            result = service.submit_song(form_data, user_id)
        else:
            result = Result(False, "", f"Unknown action: '{action}'")

        if not result.success:
            return render_error_template(form_data, result.error or "Unknown error", [])

        return render_template('member/submit_success.html', message=result.message)

    except Exception as e:
        return render_error_template(form_data, f"System error: {str(e)}", [])

#@bp.patch('/submit')
def patch_submission():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    user_data = get_user_id_from_session(session_id)
    if not user_data:
        return redirect(url_for('session.login'))

    default_user_id = user_data[0]
    permissions = get_user_permissions(default_user_id)

def render_error_template(form_data: FormData, error: str, missing_fields: list[str]):
    """Render error template with consistent data"""
    user_data = get_user_id_from_session(request.cookies.get('session'))
    user_id = user_data[0] if user_data else None

    return render_template('member/submit.html',
                         years=get_years(),
                         data=form_data.__dict__,
                         languages=get_languages(),
                         countries=get_countries(form_data.year, user_id),
                         year=form_data.year,
                         country=form_data.country,
                         selected_languages=form_data.languages,
                         error=error,
                         missing_fields=missing_fields,
                         onLoad=False,
                         users=get_users())
