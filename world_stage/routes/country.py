from collections import defaultdict
import re
from flask import Blueprint, request

from ..utils import get_countries, get_country_name, get_country_songs, get_song, get_user_id_from_session, get_user_permissions, render_template, get_markdown_parser

bp = Blueprint('country', __name__, url_prefix='/country')

@bp.get('/')
def index():
    countries = get_countries(only_participating=True)
    res = defaultdict(list)
    for c in countries:
        l = c.name[0]
        res[l].append(c)

    return render_template('country/index.html', countries=res)

@bp.get('/<code>')
def country(code: str):
    songs = get_country_songs(code.upper(), select_languages=True)
    if not songs:
        return render_template('error.html', error=f"Songs not found for country {code}")
    name = get_country_name(code.upper())
    return render_template('country/country.html', songs=songs, country=code, country_name=name)

allow_video_extensions = ('mp4', 'm4v', 'webm', 'ogg', 'mov')
mime_types = {
    'mp4': 'video/mp4',
    'm4v': 'video/x-m4v',
    'webm': 'video/webm',
    'ogg': 'video/ogg',
    'mov': 'video/quicktime'
}

def generate_iframe(url: str):
    if 'youtu.be' in url:
        video_id = url.split('/')[-1]
        return f'<iframe width="560" height="315" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>'

    elif 'youtube.com/watch' in url:
        match = re.search(r'v=([^&]+)', url)
        if match:
            video_id = match.group(1)
            return f'<iframe width="560" height="315" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>'

    elif 'drive.google.com/file/d/' in url:
        match = re.search(r'/d/([^/]+)', url)
        if match:
            file_id = match.group(1)
            return f'<iframe src="https://drive.google.com/file/d/{file_id}/preview" width="560" height="315"></iframe>'

    elif (suffix := url.rsplit('.', 1)[-1].lower()) in allow_video_extensions:
        if suffix in mime_types:
            return f'''<video width="560" height="315" controls>
            <source src="{url}" type="{mime_types[suffix]}">
            This media format isn't supported for direct playback by your browser. <a href="{url}" target="_blank">Watch the video here</a>.
            </video>'''
        else:
            return f'''This media format isn't supported for direct playback by your browser. <a href="{url}" target="_blank">Watch the video here</a>.'''

    else:
        return f'''This media format isn't supported for direct playback by your browser. <a href="{url}" target="_blank">Watch the video here</a>.'''

@bp.get('/<code>/<int:year>')
def details(code: str, year: int):
    song = get_song(year, code.upper())
    if not song:
        return render_template('error.html', error=f"Songs not found for country {code} in year {year}")
    url = song.video_link
    embed = ''
    if url and url != 'N/A':
        embed = generate_iframe(url)
    name = get_country_name(code.upper())

    session_id = request.cookies.get('session')
    user_data = get_user_id_from_session(session_id)
    user_id = None
    if user_data:
        user_id = user_data[0]
    permissions = get_user_permissions(user_id)

    can_edit = permissions.can_edit or user_id == song.submitter_id
    english_lyrics = []
    latin_lyrics = []
    native_lyrics = []
    notes = []

    md = get_markdown_parser()

    if song.english_lyrics:
        english_lyrics = md.renderInline(song.english_lyrics).split('\n')
    if song.latin_lyrics:
        latin_lyrics = md.renderInline(song.latin_lyrics).split('\n')
    if song.native_lyrics:
        native_lyrics = md.renderInline(song.native_lyrics).split('\n')
    if song.lyrics_notes:
        notes = md.renderInline(song.lyrics_notes).split('\n')

    rows = max(len(english_lyrics), len(latin_lyrics), len(native_lyrics))
    columns = (1 if english_lyrics else 0) + (1 if latin_lyrics else 0) + (1 if native_lyrics else 0)

    return render_template('country/details.html', song=song, embed=embed, country_name=name, year=year, rows=rows,
                            columns=columns,
                            native_lyrics=native_lyrics, latin_lyrics=latin_lyrics, english_lyrics=english_lyrics,
                            can_edit=can_edit, notes=notes)
