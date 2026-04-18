from collections import defaultdict
import re
from flask import Blueprint, redirect, request, url_for

from ..utils import get_countries, get_country_name, get_country_songs, get_show_results_for_songs, get_song, get_special_songs_for_country, get_special_song, get_user_id_from_session, get_user_permissions, render_template, get_markdown_parser, resolve_country_code
from ..db import get_db

bp = Blueprint('country', __name__, url_prefix='/country')

@bp.get('/')
def index():
    countries = get_countries(only_participating=True)
    res = defaultdict(list)
    for c in countries:
        l = c.name[0]
        res[l].append(c)

    return render_template('country/index.html', countries=res)

@bp.get('/<code>/bias')
def bias(code: str):
    canonical = resolve_country_code(code.upper())
    if not canonical:
        return render_template('error.html', error=f"Country not found: {code}"), 404
    if canonical.lower() != code.lower():
        return redirect(url_for('country.bias', code=canonical.lower()), 301)

    name = get_country_name(canonical)
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM country_voter_bias(%s)', (canonical,))
    biases = [dict(r) for r in cursor.fetchall()]

    return render_template('inbound_bias.html',
                           subject_type='country',
                           subject_code=canonical,
                           subject_name=name,
                           biases=biases)

@bp.get('/<code>')
def country(code: str):
    canonical = resolve_country_code(code.upper())
    if canonical and canonical.lower() != code.lower():
        return redirect(url_for('country.country', code=canonical.lower()), 301)
    songs = get_country_songs(code.upper(), select_languages=True)
    if not songs:
        return render_template('error.html', error=f"Songs not found for country {code}")
    name = get_country_name(code.upper())
    results = get_show_results_for_songs([s.id for s in songs])
    regular_songs = [s for s in songs if s.year is None or s.year >= 0]
    special_songs = [s for s in songs if s.year is not None and s.year < 0]
    return render_template('country/country.html', songs=regular_songs, special_songs=special_songs,
                           country=code, country_name=name, results=results)

mime_types = {
    'mp4': 'video/mp4',
    'm4v': 'video/mp4',
    'm4a': 'audio/mp4',
    'webm': 'video/webm',
    'ogg': 'video/ogg',
    'mov': 'video/quicktime'
}

def generate_iframe(url: str, img_url: str | None):
    if 'youtu.be' in url:
        video_id = url.split('/')[-1]
        return f'<iframe src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>'

    elif 'youtube.com/watch' in url:
        match = re.search(r'v=([^&]+)', url)
        if match:
            video_id = match.group(1)
            return f'<iframe src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>'

    elif 'drive.google.com/file/d/' in url:
        match = re.search(r'/d/([^/]+)', url)
        if match:
            file_id = match.group(1)
            return f'<iframe src="https://drive.google.com/file/d/{file_id}/preview"></iframe>'

    elif (suffix := url.rsplit('.', 1)[-1].lower()) in mime_types.keys():
        mime_type = mime_types[suffix]
        poster = ''
        if mime_type.startswith('audio'):
            poster = f'poster="{img_url}"'
        return f'''<video id="video-player"
                    class="video-js vjs-fill"
                    controls
                    {poster}
                    preload="metadata"
                    data-setup='{{"responsive": true}}'
                    src="{url}">
        This media format isn't supported for direct playback by your browser. <a href="{url}" target="_blank">Watch the video here</a>.
        </video>'''

    else:
        return f'''This media format isn't supported for direct playback by your browser. <a href="{url}" target="_blank">Watch the video here</a>.'''

@bp.get('/<code>/<int:year>')
def details(code: str, year: int):
    canonical = resolve_country_code(code.upper())
    if canonical and canonical.lower() != code.lower():
        return redirect(url_for('country.details', code=canonical.lower(), year=year), 301)
    song = get_song(year, code.upper())
    if not song:
        return render_template('error.html', error=f"Songs not found for country {code} in year {year}")
    url = song.video_link
    embed = ''
    if url and url != 'N/A':
        embed = generate_iframe(url, song.poster_link)
    name = get_country_name(code.upper())

    session_id = request.cookies.get('session')
    user_data = get_user_id_from_session(session_id)
    user_id = None
    if user_data:
        user_id = user_data[0]
    permissions = get_user_permissions(user_id)

    can_edit = permissions.can_edit or user_id == song.submitter_id
    translated_lyrics = []
    latin_lyrics = []
    native_lyrics = []
    notes = []
    sources = song.sources or ''

    md = get_markdown_parser()

    if song.translated_lyrics:
        translated_lyrics = md.renderInline(song.translated_lyrics).split('\n')
    if song.latin_lyrics:
        latin_lyrics = md.renderInline(song.latin_lyrics).split('\n')
    if song.native_lyrics:
        native_lyrics = md.renderInline(song.native_lyrics).split('\n')
    if song.lyrics_notes:
        notes = md.renderInline(song.lyrics_notes).split('\n')

    rows = max(len(translated_lyrics), len(latin_lyrics), len(native_lyrics))
    columns = (1 if translated_lyrics else 0) + (1 if latin_lyrics else 0) + (1 if native_lyrics else 0)

    song_results = get_show_results_for_songs([song.id]).get(song.id, {})

    return render_template('country/details.html', song=song, embed=embed, name=name, year=year, rows=rows,
                            columns=columns, sources=sources,
                            native_lyrics=native_lyrics, latin_lyrics=latin_lyrics, translated_lyrics=translated_lyrics,
                            can_edit=can_edit, notes=notes, song_results=song_results)

def _resolve_special(short_name: str) -> dict | None:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, status, special_name, special_short_name FROM year WHERE special_short_name = %s', (short_name,))
    return cursor.fetchone()

def _render_song_details(song, name, special_short_name, special_name):
    """Render the song details page for a special song."""
    url = song.video_link
    embed = ''
    if url and url != 'N/A':
        embed = generate_iframe(url, song.poster_link)

    session_id = request.cookies.get('session')
    user_data = get_user_id_from_session(session_id)
    user_id = user_data[0] if user_data else None
    permissions = get_user_permissions(user_id)

    can_edit = permissions.can_edit or user_id == song.submitter_id

    md = get_markdown_parser()
    translated_lyrics = md.renderInline(song.translated_lyrics).split('\n') if song.translated_lyrics else []
    latin_lyrics = md.renderInline(song.latin_lyrics).split('\n') if song.latin_lyrics else []
    native_lyrics = md.renderInline(song.native_lyrics).split('\n') if song.native_lyrics else []
    notes = md.renderInline(song.lyrics_notes).split('\n') if song.lyrics_notes else []
    sources = song.sources or ''

    rows = max(len(translated_lyrics), len(latin_lyrics), len(native_lyrics))
    columns = (1 if translated_lyrics else 0) + (1 if latin_lyrics else 0) + (1 if native_lyrics else 0)

    song_results = get_show_results_for_songs([song.id]).get(song.id, {})

    return render_template('country/details.html', song=song, embed=embed, name=name, year=special_name, rows=rows,
                            columns=columns, sources=sources,
                            native_lyrics=native_lyrics, latin_lyrics=latin_lyrics, translated_lyrics=translated_lyrics,
                            can_edit=can_edit, notes=notes, song_results=song_results,
                            special=special_short_name, special_name=special_name)

@bp.get('/<code>/<special_short_name>', defaults={'entry_number': None})
@bp.get('/<code>/<special_short_name>/<int:entry_number>')
def special_details(code: str, special_short_name: str, entry_number: int | None):
    # Don't match numeric year segments — those belong to the existing details() route
    try:
        int(special_short_name)
        return render_template('error.html', error="Not found"), 404
    except ValueError:
        pass

    canonical = resolve_country_code(code.upper())
    if canonical and canonical.lower() != code.lower():
        return redirect(url_for('country.special_details', code=canonical.lower(),
                                special_short_name=special_short_name, entry_number=entry_number), 301)

    special_year = _resolve_special(special_short_name)
    if not special_year:
        return render_template('error.html', error="Special not found"), 404

    year_id = special_year['id']
    special_name = special_year['special_name']
    name = get_country_name(code.upper())

    if entry_number is not None:
        # Direct song lookup by entry number
        song = get_special_song(year_id, code.upper(), entry_number)
        if not song:
            return render_template('error.html', error=f"Song not found for {name} in {special_name} (entry {entry_number})"), 404
        return _render_song_details(song, name, special_short_name, special_name)

    # No entry number — find all songs for this country in this special
    songs = get_special_songs_for_country(year_id, code.upper())
    if not songs:
        return render_template('error.html', error=f"No songs found for {name} in {special_name}"), 404

    if len(songs) == 1:
        # Only one entry — show it directly
        return _render_song_details(songs[0], name, special_short_name, special_name)

    # Multiple entries — show disambiguation page
    return render_template('country/special_disambig.html',
                           songs=songs, country=code, country_name=name,
                           special=special_short_name, special_name=special_name)
