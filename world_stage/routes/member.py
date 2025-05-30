from dataclasses import dataclass
import unicodedata
from flask import Blueprint, redirect, request, url_for
from typing import Optional

from ..db import get_db
from ..utils import get_user_id_from_session, format_seconds, get_years, parse_seconds, render_template

bp = Blueprint('member', __name__, url_prefix='/member')

@dataclass
class SongData:
    year: int
    country: str
    title: str
    native_title: Optional[str]
    artist: str
    is_placeholder: bool
    title_language_id: Optional[int]
    native_language_id: Optional[int]
    video_link: Optional[str]
    snippet_start: Optional[str]
    snippet_end: Optional[str]
    english_lyrics: Optional[str]
    romanized_lyrics: Optional[str]
    native_lyrics: Optional[str]
    languages: list[dict]

    def __init__(self, *, year: int, country: str, title: str,
                 native_title: Optional[str], artist: str,
                 is_placeholder: bool, title_language_id: Optional[int],
                 native_language_id: Optional[int], video_link: Optional[str],
                 snippet_start: Optional[str], snippet_end: Optional[str],
                 english_lyrics: Optional[str], romanized_lyrics: Optional[str],
                 native_lyrics: Optional[str], languages: list[dict]):
        self.year = year
        self.country = country
        self.title = title
        self.native_title = native_title
        self.artist = artist
        self.is_placeholder = is_placeholder
        self.title_language_id = title_language_id
        self.native_language_id = native_language_id
        self.video_link = video_link
        self.snippet_start = snippet_start
        self.snippet_end = snippet_end
        self.english_lyrics = english_lyrics
        self.romanized_lyrics = romanized_lyrics
        self.native_lyrics = native_lyrics
        self.languages = languages

    def as_dict(self) -> dict:
        res = {}
        for attr in self.__dict__:
                res[attr] = getattr(self, attr)
        return res

def delete_song(song_data: SongData, user_id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, submitter_id FROM song
        WHERE year_id = ? AND country_id = ?
    ''', (song_data.year, song_data.country))
    song_id = cursor.fetchone()
    if not song_id:
        return {'error': 'Song not found.'}
    song_id, submitter_id = song_id

    if submitter_id != user_id:
        return {'error': 'You are not the submitter.'}
    
    cursor.execute('''
        UPDATE song
        SET title = NULL, native_title = NULL, artist = NULL,
            is_placeholder = 1, title_language_id = NULL,
            native_language_id = NULL, video_link = NULL,
            snippet_start = NULL, snippet_end = NULL,
            translated_lyrics = NULL, romanized_lyrics = NULL,
            native_lyrics = NULL, submitter_id = NULL,
            modified_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (song_id,))

    cursor.execute('''
        DELETE FROM song_language
        WHERE song_id = ?
        ''', (song_id,))

    db.commit()

    return {'success': True, 'message': f"The song \"{song_data.artist} — {song_data.title}\" hass been deleted from {song_data.year}"}

def update_song(song_data: SongData, user_id: int) -> dict:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM song
        WHERE year_id = ? AND country_id = ?
    ''', (song_data.year, song_data.country))
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
            native_lyrics = ?, submitter_id = ?,
            modified_at = CURRENT_TIMESTAMP
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
        user_id,
        song_id
    ))

    cursor.execute('''
        DELETE FROM song_language
        WHERE song_id = ?
        ''', (song_id,))

    for i, lang_id in enumerate(song_data.languages):
        cursor.execute('''
            INSERT INTO song_language (song_id, language_id, priority)
            VALUES (?, ?, ?)
        ''', (song_id, lang_id, i))

    db.commit()

    return {'success': True, 'message': f"The song \"{song_data.artist} — {song_data.title}\" hass been submitted for {song_data.year}"}

@bp.get('/')
def index():
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

    return render_template('member/index.html', username=username)

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
    
    return render_template('member/submit.html', years=get_years(), languages=get_languages(), countries={}, data={}, onLoad=True)

@bp.get('/submit/<year>')
def get_countries_for_year(year):
    d = get_user_id_from_session(request.cookies.get('session'))
    if d:
        user_id = d[0]

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

    snippet_start = format_seconds(snippet_start) if snippet_start is not None else None
    snippet_end = format_seconds(snippet_end) if snippet_end is not None else None

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
    user_id_raw = get_user_id_from_session(session_id)
    if not user_id_raw:
        return redirect(url_for('session.login'))
    user_id = user_id_raw[0]

    languages = []
    invalid_languages = []
    other_data = {}
    action = request.form['action']
    for key, value in request.form.items():
        if key == 'action':
            continue

        if key.startswith('language'):
            n = int(key.removeprefix('language'))
            try:
                languages.append((n, int(value)))
            except ValueError:
                invalid_languages.append(key)
        else:
            other_data[key] = value.strip()
            other_data[key] = unicodedata.normalize('NFKC', other_data[key])
            other_data[key] = other_data[key].replace('\r', '')
            other_data[key] = None if other_data[key] == '' else other_data[key]

    languages.sort(key=lambda x: x[0])
    languages = list(map(lambda x: x[1], languages))

    other_data['is_placeholder'] = other_data.get('is_placeholder', False) == "on"
    is_translation = other_data.pop('is_translation', False) == "on"
    does_match = other_data.pop('does_match', False) == "on"

    if languages and (does_match or not other_data.get('native_title', None)):
        other_data['native_language_id'] = languages[0]

    if is_translation:
        other_data['title_language_id'] = 20 # English
    else:
        other_data['title_language_id'] = other_data.get('native_language_id')

    missing_fields = []
    missing_fields_internal = []
    for field in required_fields.keys():
        if field not in other_data or other_data[field] is None:
            missing_fields.append(required_fields[field])
            missing_fields_internal.append(field)
    
    if missing_fields:
        return render_template('member/submit.html', years=get_years(), data=other_data,
                               languages=get_languages(), countries=get_countries(other_data['year'], user_id),
                               year=other_data['year'], country=other_data['country'],
                               selected_languages=languages, invalid_languages=invalid_languages,
                               error='Missing required fields: ' + ', '.join(missing_fields),
                               missing_fields=missing_fields_internal, onLoad=False)

    other_data['snippet_start'] = parse_seconds(other_data['snippet_start'])
    other_data['snippet_end'] = parse_seconds(other_data['snippet_end'])

    song_data = SongData(languages=languages, **other_data)

    if action == 'delete':
        res = delete_song(song_data, user_id)
    elif action == 'submit':
        res = update_song(song_data, user_id)
    else:
        res = {'error': f"Unknown action: '{action}'."}
    
    dur = None
    if other_data['snippet_end'] is not None and other_data['snippet_start'] is not None:
        dur = other_data['snippet_end'] - other_data['snippet_start']

    if dur is not None and dur > 20:
        res = {'error': f"The maximum length of the recap snippet is 20 seconds. Yours is {dur} seconds long."}
    
    if 'error' in res:
        return render_template('member/submit.html', years=get_years(), data=other_data,
                               languages=get_languages(), countries=get_countries(other_data['year'], user_id),
                               year=other_data['year'], country=other_data['country'],
                               selected_languages=languages, invalid_languages=invalid_languages,
                               error=res['error'], missing_fields=missing_fields_internal, onLoad=False)

    return render_template('member/submit_success.html', message=res['message'])