from collections import defaultdict
import urllib.parse
import unicodedata
from flask import Blueprint, request

from ..utils import get_user_songs, get_show_results_for_songs, render_template
from ..db import fetchone, get_db

bp = Blueprint('user', __name__, url_prefix='/user')

@bp.get('/')
def index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, username, role FROM account
        ORDER BY username
    ''')
    users = defaultdict(list)
    users['Admin'] = []
    for row in cursor.fetchall():
        if row['role'] == 'admin' or row['role'] == 'owner':
            users['Admin'].append({
                'id': row['id'],
                'username': row['username']
            })
        first_letter = row['username'][0].upper()
        val = {
            'id': row['id'],
            'username': row['username']
        }
        users[first_letter].append(val)

    return render_template('user/index.html', users=users)

@bp.get('/<username>')
def profile(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    return render_template('user/page.html', username=username)

def redact_song_if_show(song: dict, year: int, show_short_name: str, status: str) -> tuple[bool, bool]:
    db = get_db()
    cursor = db.cursor()
    show_exists = False
    song_modified = False

    cursor.execute('''
        SELECT id FROM show WHERE year_id = %s AND short_name = %s
    ''', (year, show_short_name))
    show = cursor.fetchone()
    if show:
        show_exists = True
        cursor.execute('''
            SELECT COUNT(*) AS c FROM song_show
            WHERE show_id = %s AND song_id = %s
        ''', (show['id'], song['id']))
        if fetchone(cursor)['c'] > 0:
            song_modified = True
            song['class'] = f'qualifier {show_short_name}-qualifier'
            if status == 'partial':
                    song['title'] = ''
                    song['artist'] = ''
                    song['country'] = ''
                    song['code'] = 'XX'

    return (show_exists, song_modified)


@bp.get('/<username>/votes')
def votes(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM account WHERE username = %s
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id['id']

    cursor.execute('''
        SELECT vote_set.id, vote_set.show_id, account.username, nickname, country_id,
               show.show_name, show.short_name, show.date, show.year_id, show.status,
               year.special_name, year.special_short_name
        FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN show ON vote_set.show_id = show.id
        LEFT JOIN year ON show.year_id = year.id
        WHERE vote_set.voter_id = %s AND (show.status = 'full' OR show.status = 'partial')
        ORDER BY show.date DESC
    ''', (user_id,))
    votes = []
    for row in cursor.fetchall():
        val = {
            'id': row['id'],
            'show_id': row['show_id'],
            'username': row['username'],
            'nickname': row['nickname'] or username,
            'code': row['country_id'],
            'show_name': row['show_name'],
            'short_name': row['short_name'],
            'status': row['status'],
            'date': row['date'].strftime("%d %b %Y"),
            'year': row['year_id'],
            'special_name': row['special_name'],
            'special_short_name': row['special_short_name'],
        }
        votes.append(val)

    # Batch-fetch show results for all shows this user voted in,
    # keyed by (show_id, song_id) → place.
    show_ids = list({v['show_id'] for v in votes})
    show_results: dict[tuple[int, int], int] = {}
    if show_ids:
        cursor.execute('''
            SELECT show_id, song_id, place
            FROM country_show_results
            WHERE show_id = ANY(%s)
        ''', (show_ids,))
        for row in cursor.fetchall():
            show_results[(row['show_id'], row['song_id'])] = row['place']

    for vote in votes:
        cursor.execute('''
            SELECT score AS pts, song.title, song.artist, song.country_id AS code, country.name, song.id FROM vote
            JOIN song ON vote.song_id = song.id
            JOIN country ON song.country_id = country.id
            WHERE vote.vote_set_id = %s
            ORDER BY score DESC
        ''', (vote['id'],))
        songs = []
        for val in cursor.fetchall():
            if vote['short_name'] != 'f':
                redact_song_if_show(val, vote['year'], 'f', vote['status'])
                if vote['short_name'] != 'sc':
                    redact_song_if_show(val, vote['year'], 'sc', vote['status'])
            # Only show result placement for non-redacted songs.
            if val.get('code') == 'XX':
                val['result_place'] = None
            else:
                val['result_place'] = show_results.get((vote['show_id'], val['id']))
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
        SELECT id FROM account WHERE username = %s
    ''', (username,))
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id_g['id']

    songs = get_user_songs(user_id, select_languages=True)
    results = get_show_results_for_songs([s.id for s in songs])

    regular_songs = [s for s in songs if s.year is None or s.year >= 0]
    special_songs = [s for s in songs if s.year is not None and s.year < 0]

    return render_template('user/submissions.html',
                           songs=regular_songs, special_songs=special_songs,
                           username=username, results=results)

def get_country_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM user_country_bias(%s)', (user_id,))
    for r in cursor.fetchall():
        yield dict(r)


def get_submitter_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM user_submitter_bias(%s)', (user_id,))
    for r in cursor.fetchall():
        yield dict(r)


@bp.get('/<username>/bias')
def bias(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    bias_type = request.args.get('type', 'country')

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM account WHERE username = %s
    ''', (username,))
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return {'error': 'User not found'}, 404
    user_id = user_id_g['id']

    if bias_type == 'user':
        biases = get_submitter_biases(user_id)
    elif bias_type == 'country':
        biases = get_country_biases(user_id)
    else:
        return render_template('error.html', error=f"Invalid bias type specified: {bias_type}."), 400

    return render_template('user/bias.html', username=username, bias_type=bias_type, biases=biases)

@bp.get('/<username>/bias/for')
def bias_for(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id FROM account WHERE username = %s', (username,))
    row = cursor.fetchone()
    if not row:
        return render_template('error.html', error="User not found"), 404

    cursor.execute('SELECT * FROM submitter_voter_bias(%s)', (row['id'],))
    biases = [dict(r) for r in cursor.fetchall()]

    return render_template('inbound_bias.html',
                           subject_type='user',
                           subject_name=username,
                           biases=biases)