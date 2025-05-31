from collections import defaultdict
from typing import Optional
from flask import make_response, request, Blueprint
import datetime
import unicodedata

from ..utils import format_timedelta, get_show_id, get_countries, dt_now, get_show_songs, get_user_id_from_session, get_vote_count_for_show, render_template
from ..db import get_db

bp = Blueprint('vote', __name__, url_prefix='/vote')

def update_votes(voter_id, nickname, country_id, point_system_id, votes, show_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = ? AND show_id = ?', (voter_id,show_id))
    vote_set_id = cursor.fetchone()[0]

    cursor.execute('UPDATE vote_set SET nickname = ?, country_id = ? WHERE id = ?', (nickname, country_id or 'XXX', vote_set_id))

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
            RETURNING id
            ''', (voter_id, show_id, country_id or 'XXX', nickname))
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
        update_votes(voter_id, nickname, country_id, point_system_id, votes, show_id)
        action = "updated"

    db.commit()

    return action

@bp.get('/')
def index():
    open_votings = []

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, show_name, short_name, year_id, voting_opens, voting_closes
        FROM show
        WHERE voting_opens <= datetime('now') AND voting_closes >= datetime('now')
    ''')

    for id, name, short_name, year, voting_opens, voting_closes in cursor.fetchall():
        left = voting_closes - dt_now()
        open_votings.append({
            'id': id,
            'name': f"{year} {name}" if year else name,
            'short_name': f"{year}-{short_name}" if year else short_name,
            'voting_opens': voting_opens,
            'voting_closes': voting_closes,
            'left': format_timedelta(left),
        })
    return render_template('vote/index.html', shows=open_votings)

@bp.get('/<show>')
def vote(show: str):
    username = request.cookies.get('username', '')
    session_id = request.cookies.get('session')
    nickname = None
    country = ''
    country_id = ''

    selected = {}

    show_data = get_show_id(show)

    if not show_data or not show_data.id:
        return render_template('error.html', error="Show not found"), 404

    if (show_data.voting_opens > dt_now()
        or show_data.voting_closes < dt_now()):
        return render_template('error.html', error="Voting is closed"), 400

    db = get_db()
    cursor = db.cursor()

    if session_id:
        d = get_user_id_from_session(session_id)
        if d:
            _, username = d

    vote_set_id = None
    if username:
        cursor.execute('''
            SELECT vote_set.id, vote_set.nickname, vote_set.country_id
            FROM vote_set
            JOIN user ON vote_set.voter_id = user.id
            WHERE user.username = ? AND vote_set.show_id = ?
        ''', (username, show_data.id))
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

    songs = get_show_songs(show_data.year, show_data.short_name)

    return render_template('vote/vote.html',
                           songs=songs, points=show_data.points, selected=selected,
                           username=username, nickname=nickname, country=country,
                           year=show_data.year, show_name=show_data.name, show=show,
                           selected_country=country_id, countries=get_countries(),
                           vote_count=get_vote_count_for_show(show_data.id))

@bp.post('/<show>')
def vote_post(show: str):
    votes = {}
    invalid = []
    username = ''
    nickname = ''

    show_data = get_show_id(show)

    if not show_data or not show_data.id:
        return render_template('error.html', error="Show not found"), 404

    if (show_data.voting_opens > dt_now()
        or show_data.voting_closes < dt_now()):
        return render_template('error.html', error="Voting is closed"), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, title, artist, running_order
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
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

    username_invalid = False
    username = request.form['username']
    username = unicodedata.normalize('NFKC', username)
    nickname = request.form['nickname']
    country_id: Optional[str] = request.form['country']
    if not country_id:
        country_id = None

    if not username:
        username_invalid = True
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
    for point in show_data.points:
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

    invalid_votes: dict[int, list[int]] = defaultdict(list)
    for point, song_id in votes.items():
        invalid_votes[song_id].append(point)

    invalid_votes = {k: v for k, v in invalid_votes.items() if len(v) > 1}
    invalid.extend(item for sublist in invalid_votes.values() for item in sublist)

    if invalid_votes:
        errors.append(f"Duplicate votes: {'; '.join(map(lambda v: f"{', '.join(map(str, v))} points", invalid_votes.values()))}")

    if not errors:
        action = add_votes(username, nickname or None, country_id, show_data.id, show_data.point_system_id, votes)
        resp = make_response(render_template('vote/success.html', action=action))
        resp.set_cookie('username', username, max_age=datetime.timedelta(days=30))
        return resp

    return render_template('vote/vote.html',
                           songs=songs, points=show_data.points, errors=errors,
                           selected=votes, invalid=invalid,
                           username=username, username_invalid=username_invalid, nickname=nickname,
                           year=show_data.year, show_name=show_data.name, show=show,
                           selected_country=country_id, countries=get_countries())