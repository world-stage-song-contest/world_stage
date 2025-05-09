from collections import defaultdict
from flask import render_template, request, redirect, url_for, Blueprint
import datetime
import unicodedata

from .utils import format_timedelta, get_show_id, get_countries
from .db import get_db

bp = Blueprint('vote', __name__, url_prefix='/vote')

def update_votes(voter_id, nickname, country_id, point_system_id, votes):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
    vote_set_id = cursor.fetchone()[0]

    cursor.execute('UPDATE vote_set SET nickname = ?, country_id = ? WHERE id = ?', (nickname, country_id, vote_set_id))

    for point, song_id in votes.items():
        cursor.execute('''
            SELECT id FROM point 
            WHERE point_system_id = ? AND score = ?
            ''', (point_system_id, point))
        point_id = cursor.fetchone()[0]
        cursor.execute('''
            UPDATE vote
            SET song_id = ?
            WHERE vote_set_id = ? AND point_id = ?
        ''', (song_id, vote_set_id, point_id))

def add_votes(username, nickname, country_id, show_id, point_system_id, votes):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('INSERT OR IGNORE INTO user (username) VALUES (?)', (username,))
    cursor.execute('SELECT id FROM user WHERE username = ?', (username,))
    voter_id = cursor.fetchone()[0]

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = ? AND show_id = ?', (voter_id, show_id))
    existing_vote_set = cursor.fetchone()

    if not existing_vote_set:
        cursor.execute('''
            INSERT INTO vote_set (voter_id, show_id, country_id, nickname, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ''', (voter_id, show_id, country_id, nickname))
        cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
        vote_set_id = cursor.fetchone()[0]
        for point, song_id in votes.items():
            cursor.execute('''
                SELECT id FROM point 
                WHERE point_system_id = ? AND score = ?
                ''', (point_system_id, point))
            point_id = cursor.fetchone()[0]
            cursor.execute('INSERT INTO vote (vote_set_id, song_id, point_id) VALUES (?, ?, ?)', (vote_set_id, song_id, point_id))
        action = "added"
    else:
        update_votes(voter_id, nickname, country_id, point_system_id, votes)
        action = "updated"
    
    db.commit()

    return action


@bp.get('/')
def vote_index():
    open_votings = []

    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT id, show_name, short_name, year_id, voting_opens, voting_closes
        FROM show
        WHERE voting_opens <= datetime('now') AND voting_closes >= datetime('now')
    ''')

    for id, name, short_name, year, voting_opens, voting_closes in cursor.fetchall():
        left = datetime.datetime.strptime(voting_closes, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc) - datetime.datetime.now(tz=datetime.timezone.utc)
        open_votings.append({
            'id': id,
            'name': f"{year} {name}" if year else name,
            'short_name': short_name,
            'voting_opens': voting_opens,
            'voting_closes': voting_closes,
            'left': format_timedelta(left),
        })
    return render_template('open_votings.html', shows=open_votings)

@bp.post('/<show>')
def vote_post(show: str):
    votes = {}
    invalid = []
    username = ''
    nickname = ''

    show_data = get_show_id(show)
    show_id = show_data['id']
    show_name = show_data['show_name']
    voting_opens = show_data['voting_opens']
    voting_closes = show_data['voting_closes']
    point_system_id = show_data['point_system_id']
    year = show_data['year']
    points = show_data['points']

    if not show_id:
        return redirect(url_for('main.error', error="Show not found"))
    
    if voting_opens > datetime.datetime.now(datetime.timezone.utc) or voting_closes < datetime.datetime.now(datetime.timezone.utc):
        return redirect(url_for('main.error', error="Voting is closed"))

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, title, artist, running_order
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_id,))
    songs = []
    for id, title, artist, running_order in cursor.fetchall():
        val = {
            'id': id,
            'title': title,
            'artist': artist,
            'running_order': running_order
        }
        songs.append(val)

    errors = []

    username = request.form['username']
    username = unicodedata.normalize('NFKC', username)
    nickname = request.form['nickname']
    country_id = request.form['country']
    if not country_id:
        country_id = None

    if not username:
        errors.append("Username is required.")

    cursor.execute('SELECT id FROM user WHERE username = ? COLLATE NOCASE', (username,))
    voter = cursor.fetchone()
    if voter:
        voter_id = voter[0]
    else:
        voter_id = 0

    cursor.execute('''
        SELECT id FROM song WHERE submitter_id = ?
    ''', (voter_id,))
    submitted_song = cursor.fetchone()
    if submitted_song:
        submitted_song = submitted_song[0]
    else:
        submitted_song = None

    missing = []
    for point in points:
        id_str = request.form.get(f'pts-{point}')
        if not id_str:
            missing.append(point)
            continue
        song_id = int(id_str)
        if song_id == submitted_song:
            errors.append(f"You cannot vote for your own song ({point} points).")
            invalid.append(point)
        votes[point] = song_id

    if missing:
        errors.append(f"Missing votes for {', '.join(map(str, missing))} points.")
        invalid.extend(missing)

    invalid_votes = defaultdict(list)
    for point, song_id in votes.items():
        invalid_votes[song_id].append(point)
    
    invalid_votes = {k: v for k, v in invalid_votes.items() if len(v) > 1}
    invalid.extend(item for sublist in invalid_votes.values() for item in sublist)

    if invalid_votes:
        errors.append(f"Duplicate votes: {'; '.join(map(lambda v: f"{', '.join(map(str, v))} points", invalid_votes.values()))}")

    if not errors:
        action = add_votes(username, nickname or None, country_id, show_id, point_system_id, votes)
        resp = redirect(url_for('main.success', action=action))
        resp.set_cookie('username', username, max_age=datetime.timedelta(days=30))
        return resp

    return render_template('vote.html',
                           songs=songs, points=points, errors=errors,
                           selected=votes, invalid=invalid,
                           username=username, nickname=nickname,
                           year=year, show_name=show_name, show=show,
                           selected_country=country_id, countries=get_countries())

@bp.get('/<show>')
def vote(show: str):
    username = request.cookies.get('username')
    nickname = None
    country = ''
    country_id = ''

    selected = {}

    show_data = get_show_id(show)
    show_id = show_data['id']
    show_name = show_data['show_name']
    voting_opens = show_data['voting_opens']
    voting_closes = show_data['voting_closes']
    year = show_data['year']
    points = show_data['points']

    if not show_id:
        return redirect(url_for('main.error', error="Show not found"))
    
    if voting_opens > datetime.datetime.now(datetime.timezone.utc) or voting_closes < datetime.datetime.now(datetime.timezone.utc):
        return redirect(url_for('main.error', error="Voting is closed"))
    
    db = get_db()
    cursor = db.cursor()

    vote_set_id = None
    if username:
        cursor.execute('''
            SELECT vote_set.id, vote_set.nickname, vote_set.country_id
            FROM vote_set
            JOIN user ON vote_set.voter_id = user.id
            WHERE user.username = ? AND vote_set.show_id = ?
        ''', (username, show_id))
        vote_set_id = cursor.fetchone()
        if vote_set_id:
            vote_set_id, nickname, country_id = vote_set_id

    if vote_set_id:
        cursor.execute('''
            SELECT song_id, score FROM vote
            JOIN point ON vote.point_id = point.id
            WHERE vote_set_id = ?
        ''', (vote_set_id,))
        for song_id, pts in cursor.fetchall():
            selected[pts] = song_id

    cursor.execute('''
        SELECT song.id, title, artist, running_order
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_id,))
    songs = []
    for id, title, artist, running_order in cursor.fetchall():
        val = {
            'id': id,
            'title': title,
            'artist': artist,
            'running_order': running_order
        }
        songs.append(val)
    
    return render_template('vote.html',
                           songs=songs, points=points, selected=selected,
                           username=username, nickname=nickname, country=country,
                           year=year, show_name=show_name, show=show,
                           selected_country=country_id, countries=get_countries())
