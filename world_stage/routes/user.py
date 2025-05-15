from dataclasses import dataclass
import unicodedata
from flask import Blueprint, redirect, render_template, request, url_for

from ..db import get_db
from ..utils import get_user_id_from_session, format_seconds, parse_seconds

bp = Blueprint('user', __name__, url_prefix='/user')

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
    snippet_start: str | None
    snippet_end: str | None
    english_lyrics: str | None
    romanized_lyrics: str | None
    native_lyrics: str | None
    languages: list[dict]

    def as_dict(self) -> dict:
        res = {}
        for attr in self.__dict__:
                res[attr] = getattr(self, attr)
        return res

def update_song(song_data: SongData, user_id: int) -> dict:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM song
        WHERE year_id = ? AND country_id = ? AND submitter_id = ?
    ''', (song_data.year, song_data.country, user_id))
    song_id = cursor.fetchone()
    if not song_id:
        return {'error': 'Song not found.'}
    song_id = song_id[0]

    cursor.execute('''
        UPDATE song
        SET title = ?, native_title = ?, artist = ?,
            is_placeholder = ?, title_language_id = ?,
            native_language_id = ?, video_link = ?,
            snippet_start = ?, snippet_end = ?,
            translated_lyrics = ?, romanized_lyrics = ?,
            native_lyrics = ?
        WHERE id = ?
    ''', (
        song_data.title,
        song_data.native_title,
        song_data.artist,
        int(song_data.is_placeholder),
        song_data.title_language_id,
        song_data.native_language_id,
        song_data.video_link,
        song_data.snippet_start,
        song_data.snippet_end,
        song_data.english_lyrics,
        song_data.romanized_lyrics,
        song_data.native_lyrics,
        song_id
    ))

    for i, lang_id in enumerate(song_data.languages):
        cursor.execute('''
            INSERT INTO song_language (song_id, language_id, priority)
            VALUES (?, ?, ?)
            ON CONFLICT(song_id, priority) DO UPDATE SET language_id = ?
        ''', (song_id, lang_id, i, lang_id))

    db.commit()

    return {'success': True}

@bp.get('/')
def user_index():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT user.username, user.id
        FROM session
        JOIN user ON session.user_id = user.id
        WHERE session_id = ?
    ''', (session_id,))

    username = cursor.fetchone()
    if not username:
        resp = redirect(url_for('session.login'))
        resp.delete_cookie('session')
        return resp
    username = username[0]

    return render_template('user/index.html', username=username)

def get_years() -> list[int]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id FROM year
        WHERE closed = 0
    ''')
    return list(map(lambda x: x[0], cursor.fetchall()))

def get_languages() -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, name FROM language
        ORDER BY name
    ''')
    return list(map(lambda x: {'id': x[0], 'name': x[1]}, cursor.fetchall()))

def get_countries(year: int, user_id: int) -> dict[str, list[dict]]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM song
        WHERE submitter_id = ? AND year_id = ? AND is_placeholder = 0
    ''', (user_id, year))
    count = cursor.fetchone()[0]

    countries: dict[str, list] = {'own': [], 'placeholder': []}

    cursor.execute('''
        SELECT country.name, country.id FROM song
        JOIN country ON song.country_id = country.id
        WHERE song.year_id = ? AND song.submitter_id = ?
        ORDER BY country.name
    ''', (year,user_id))
    for name, cc in cursor.fetchall():
        countries['own'].append({'name': name, 'cc': cc})

    if count < 2:
        cursor.execute('''
            SELECT country.name, country.id FROM song
            JOIN country ON song.country_id = country.id
            WHERE song.year_id = ? AND song.is_placeholder = 1
            ORDER BY country.name
        ''', (year,))
        for name, cc in cursor.fetchall():
            countries['placeholder'].append({'name': name, 'cc': cc})

    return countries

@bp.get('/submit')
def submit():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))
    
    return render_template('user/submit.html', years=get_years(), languages=get_languages(), countries={}, data={}, onLoad=True)

@bp.get('/submit/<year>')
def get_countries_for_year(year):
    user_id = get_user_id_from_session(request.cookies.get('session'))

    return {'countries': get_countries(year, user_id)}

@bp.get('/submit/<year>/<country>')
def get_country_data(year, country):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, title, native_title, artist, is_placeholder,
                   title_language_id, native_language_id,
                   video_link, snippet_start, snippet_end,
                   translated_lyrics, romanized_lyrics, native_lyrics
        FROM song
        WHERE year_id = ? AND country_id = ?
    ''', (year, country))
    song = cursor.fetchone()
    if not song:
        return {'error': 'Song not found'}, 404

    (id, title, native_title, artist, is_placeholder,
     title_language_id, native_language_id,
     video_link, snippet_start, snippet_end,
     translated_lyrics, romanized_lyrics, native_lyrics) = song

    snippet_start = format_seconds(snippet_start) if snippet_start else None
    snippet_end = format_seconds(snippet_end) if snippet_end else None

    cursor.execute('''
        SELECT language.id, name FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_id = ?
        ORDER BY priority
    ''', (id,))
    languages = []
    for lang_id, name in cursor.fetchall():
        languages.append({'id': lang_id, 'name': name})
    
    song_data = SongData(
        year=year,
        country=country,
        title=title,
        native_title=native_title,
        artist=artist,
        is_placeholder=bool(is_placeholder),
        title_language_id=title_language_id,
        native_language_id=native_language_id,
        video_link=video_link,
        snippet_start=snippet_start,
        snippet_end=snippet_end,
        english_lyrics=translated_lyrics,
        romanized_lyrics=romanized_lyrics,
        native_lyrics=native_lyrics,
        languages=languages
    )

    return song_data.as_dict()

required_fields = {
    'artist': "Artist",
    'title': "Latin title",
    'video_link': "Video URL"
}

@bp.post('/submit')
def submit_song_post():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))
    user_id = get_user_id_from_session(session_id)
    if not user_id:
        return redirect(url_for('session.login'))

    languages = []
    invalid_languages = []
    other_data = {}
    for key, value in request.form.items():
        if key.startswith('language'):
            n = int(key.removeprefix('language'))
            try:
                languages.append((n, int(value or 0)))
            except ValueError:
                invalid_languages.append(key)
        else:
            other_data[key] = value.strip()
            other_data[key] = unicodedata.normalize('NFKC', other_data[key])
            other_data[key] = None if other_data[key] == '' else other_data[key]

    languages.sort(key=lambda x: x[0])
    languages = list(map(lambda x: x[1], languages))

    other_data['is_placeholder'] = other_data.get('is_placeholder', False) == "on"
    is_translation = other_data.pop('is_translation', False) == "on"
    does_match = other_data.pop('does_match', False) == "on"

    if does_match or not other_data.get('native_title', None):
        other_data['native_language_id'] = languages[0]

    if is_translation:
        other_data['title_language_id'] = 20 # English
    else:
        other_data['title_language_id'] = other_data['native_language_id']

    missing_fields = []
    missing_fields_internal = []
    for field in required_fields.keys():
        if field not in other_data or other_data[field] is None:
            missing_fields.append(required_fields[field])
            missing_fields_internal.append(field)
    
    if missing_fields:
        return render_template('user/submit.html', years=get_years(), data=other_data,
                               languages=get_languages(), countries=get_countries(other_data['year'], user_id),
                               year=other_data['year'], country=other_data['country'],
                               selected_languages=languages, invalid_languages=invalid_languages,
                               error='Missing required fields: ' + ', '.join(missing_fields),
                               missing_fields=missing_fields_internal, onLoad=False)

    other_data['snippet_start'] = parse_seconds(other_data['snippet_start'])
    other_data['snippet_end'] = parse_seconds(other_data['snippet_end'])

    song_data = SongData(languages=languages, **other_data)
    update_song(song_data, user_id)

    return render_template('user/submit_success.html')