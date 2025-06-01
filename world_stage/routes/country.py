from collections import defaultdict
import re
from flask import Blueprint

from ..utils import get_countries, get_country_name, get_country_songs, get_song, render_template

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

def generate_iframe(url: str):
    if 'youtu.be' in url:
        video_id = url.split('/')[-1]
        return f'<iframe width="560" height="315" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>'

    elif 'youtube.com/watch' in url:
        match = re.search(r'v=([^&]+)', url)
        if match:
            video_id = match.group(1)
            return f'<iframe width="560" height="315" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>'

    elif url.endswith('.mp4'):
        return f'<video width="560" height="315" controls><source src="{url}" type="video/mp4">Your browser does not support the video tag.</video>'

    else:
        return f'<!-- Unsupported URL: {url} -->'

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

    english_lyrics = []
    latin_lyrics = []
    native_lyrics = []

    if song.english_lyrics:
        english_lyrics = song.english_lyrics.split('\n')
        print(english_lyrics)
    if song.latin_lyrics:
        latin_lyrics = song.latin_lyrics.split('\n')
    if song.native_lyrics:
        native_lyrics = song.native_lyrics.split('\n')
    return render_template('country/details.html', song=song, embed=embed, country_name=name, year=year,
                           native_lyrics=native_lyrics, latin_lyrics=latin_lyrics, english_lyrics=english_lyrics)
