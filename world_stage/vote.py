from collections import defaultdict
import sqlite3
from flask import render_template, request, redirect, url_for, Blueprint
import datetime

from .utils import add_votes, format_timedelta, get_show_id
from .db import get_db

bp = Blueprint('vote', __name__, url_prefix='/vote')

@bp.get('/')
def vote_index():
    open_votings = []

    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT show.id, show.show_name, show.short_name, year.year, show.voting_opens, show.voting_closes
        FROM show
        LEFT OUTER JOIN year ON show.year_id = year.id
        WHERE show.voting_opens <= datetime('now') AND show.voting_closes >= datetime('now')
    ''')

    for id, name, short_name, year, voting_opens, voting_closes in cursor.fetchall():
        left = datetime.datetime.strptime(voting_closes, '%Y-%m-%d %H:%M') - datetime.datetime.now()
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
    
    if voting_opens > datetime.datetime.now() or voting_closes < datetime.datetime.now():
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

    if not username:
        errors.append("Username is required.")

    cursor.execute('SELECT id FROM user WHERE username = ?', (username,))
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

    for point in points:
        song_id = int(request.form.get(f'pts-{point}'))
        if song_id == submitted_song:
            errors.append(f"You cannot vote for your own song ({point} points).")
            invalid.append(point)
        votes[point] = song_id

    invalid_votes = defaultdict(list)
    for point, song_id in votes.items():
        invalid_votes[song_id].append(point)
    
    invalid_votes = {k: v for k, v in invalid_votes.items() if len(v) > 1}
    invalid.extend(item for sublist in invalid_votes.values() for item in sublist)

    if invalid_votes:
        errors.append(f"Duplicate votes.")

    if not errors:
        action = add_votes(username, nickname or None, show_id, point_system_id, votes)
        resp = redirect(url_for('main.success', action=action))
        resp.set_cookie('username', username)
        return resp

    return render_template('vote.html',
                           songs=songs, points=points, errors=errors,
                           selected=votes, invalid=invalid,
                           username=username, nickname=nickname,
                           year=year, show_name=show_name, show=show)

@bp.get('/<show>')
def vote(show: str):
    username = request.cookies.get('username')
    nickname = None
    country = ''

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
    
    if voting_opens > datetime.datetime.now() or voting_closes < datetime.datetime.now():
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
                           year=year, show_name=show_name, show=show)
