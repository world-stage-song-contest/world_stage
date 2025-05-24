from collections import defaultdict
import urllib.parse
import unicodedata
from flask import Blueprint

from ..utils import get_user_songs, render_template
from ..db import get_db

bp = Blueprint('user', __name__, url_prefix='/user')

@bp.get('/')
def index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, username FROM user
        ORDER BY username
    ''')
    users = defaultdict(list)
    for id, username in cursor.fetchall():
        first_letter = username[0].upper()
        val = {
            'id': id,
            'username': username
        }
        users[first_letter].append(val)

    return render_template('user/index.html', users=users)

@bp.get('/<username>')
def profile(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    return render_template('user/page.html', username=username)

@bp.get('/<username>/votes')
def votes(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM user WHERE username = ?
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id[0]

    cursor.execute('''
        SELECT vote_set.id, user.username, nickname, country_id, show.show_name, show.short_name, show.date, show.year_id FROM vote_set
        JOIN user ON vote_set.voter_id = user.id
        JOIN show ON vote_set.show_id = show.id
        WHERE vote_set.voter_id = ?
        ORDER BY show.date DESC
    ''', (user_id,))
    votes = []
    for id, username, nickname, country_id, show_name, short_name, date, year in cursor.fetchall():
        val = {
            'id': id,
            'username': username,
            'nickname': nickname or username,
            'code': country_id,
            'show_name': show_name,
            'short_name': short_name,
            'date': date.strftime("%d %b %Y"),
            'year': year
        }
        votes.append(val)
    
    for vote in votes:
        cursor.execute('''
            SELECT point.score, song.title, song.artist, song.country_id, country.name FROM vote
            JOIN song ON vote.song_id = song.id
            JOIN point ON vote.point_id = point.id
            JOIN country ON song.country_id = country.id
            WHERE vote.vote_set_id = ?
            ORDER BY point.score DESC
        ''', (vote['id'],))
        songs = []
        for pts, title, artist, country_id, country in cursor.fetchall():
            val = {
                'pts': pts,
                'title': title,
                'artist': artist,
                'code': country_id,
                'country': country
            }
            songs.append(val)
        vote['points'] = songs

    return render_template('user/votes.html', votes=votes, username=username)

@bp.get('/<username>/submissions')
def submissions(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM user WHERE username = ?
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id[0]

    songs = get_user_songs(user_id)

    return render_template('user/submissions.html', songs=songs, username=username)