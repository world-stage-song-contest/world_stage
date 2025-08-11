from collections import defaultdict
from typing import Optional
from flask import make_response, request, Blueprint
import datetime
import unicodedata

from ..utils import format_timedelta, get_show_id, get_countries, dt_now, get_show_songs, get_user_id_from_session, get_user_songs, get_vote_count_for_show, render_template
from ..db import get_db

bp = Blueprint('vote', __name__, url_prefix='/vote')

def update_votes(voter_id, nickname, country_id, point_system_id, votes, show_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = %s AND show_id = %s', (voter_id,show_id))
    vote_set_id = cursor.fetchone()['id'] # type: ignore

    cursor.execute('UPDATE vote_set SET nickname = %s, country_id = %s WHERE id = %s', (nickname, country_id or 'XXX', vote_set_id))

    cursor.execute('UPDATE vote SET song_id = NULL WHERE vote_set_id = %s', (vote_set_id,))

    for point, song_id in votes.items():
        cursor.execute('''
            SELECT id FROM point
            WHERE point_system_id = %s AND score = %s
            ''', (point_system_id, point))
        point_id = cursor.fetchone()['id'] # type: ignore
        cursor.execute('''
            UPDATE vote
            SET song_id = %s
            WHERE vote_set_id = %s AND point_id = %s
        ''', (song_id, vote_set_id, point_id))

def add_votes(username, nickname, country_id, show_id, point_system_id, votes):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('INSERT INTO account (username) VALUES (%s) ON CONFLICT DO NOTHING', (username,))
    cursor.execute('SELECT id FROM account WHERE username = %s', (username,))
    voter_id = cursor.fetchone()['id'] # type: ignore

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = %s AND show_id = %s', (voter_id, show_id))
    existing_vote_set = cursor.fetchone()

    if not existing_vote_set:
        cursor.execute('''
            INSERT INTO vote_set (voter_id, show_id, country_id, nickname, created_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id
            ''', (voter_id, show_id, country_id or 'XXX', nickname))
        vote_set_id = cursor.fetchone()['id'] # type: ignore
        for point, song_id in votes.items():
            cursor.execute('''
                SELECT id FROM point
                WHERE point_system_id = %s AND score = %s
                ''', (point_system_id, point))
            point_id = cursor.fetchone()['id'] # type: ignore
            cursor.execute('INSERT INTO vote (vote_set_id, song_id, point_id) VALUES (%s, %s, %s)', (vote_set_id, song_id, point_id))
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
        SELECT id, show_name AS name, short_name, year_id AS year, voting_opens, voting_closes
        FROM show
        WHERE voting_opens <= CURRENT_TIMESTAMP AND (voting_closes IS NULL OR voting_closes >= CURRENT_TIMESTAMP)
    ''')

    for row in cursor.fetchall():
        left = None
        if row['voting_closes']:
            left = row['voting_closes'] - dt_now()
        open_votings.append({
            'id': row['id'],
            'name': f"{row['year']} {row['name']}" if row['year'] else row['name'],
            'short_name': f"{row['year']}-{row['short_name']}" if row['year'] else row['short_name'],
            'voting_opens': row['voting_opens'],
            'voting_closes': row['voting_closes'],
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

    if (show_data.voting_opens and show_data.voting_opens > dt_now()
        or show_data.voting_closes and show_data.voting_closes < dt_now()):
        return render_template('error.html', error="Voting is closed"), 400

    db = get_db()
    cursor = db.cursor()

    if session_id:
        d = get_user_id_from_session(session_id)
        if d:
            _, username = d

    vote_set_id = None
    countries = []
    if username:
        cursor.execute('SELECT id FROM account WHERE username = %s COLLATE NOCASE', (username,))
        user_id = cursor.fetchone()
        if user_id:
            user_songs = get_user_songs(user_id['id'], show_data.year)
            countries = list(map(lambda s: s.country, user_songs))
            cursor.execute('''
                SELECT vote_set.id, vote_set.nickname, vote_set.country_id
                FROM vote_set
                JOIN account ON vote_set.voter_id = account.id
                WHERE account.username = %s AND vote_set.show_id = %s
            ''', (username, show_data.id))
            vs_row = cursor.fetchone()
            if vs_row:
                vote_set_id = vs_row['vote_set_id']
                nickname = vs_row['nickname']
                country_id = vs_row['country_id']

    if not countries:
        countries = get_countries()

    if vote_set_id:
        cursor.execute('''
            SELECT song_id, score FROM vote
            JOIN point ON vote.point_id = point.id
            WHERE vote_set_id = %s
        ''', (vote_set_id,))
        for song_id, pts in cursor.fetchall():
            selected[pts] = song_id

    songs = get_show_songs(show_data.year, show_data.short_name)

    return render_template('vote/vote.html',
                           songs=songs, points=show_data.points, selected=selected,
                           username=username, nickname=nickname, country=country,
                           year=show_data.year, show_name=show_data.name,
                           short_name=show_data.short_name, show=show,
                           selected_country=country_id, countries=countries,
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

    if (show_data.voting_opens and show_data.voting_opens > dt_now()
        or show_data.voting_closes and show_data.voting_closes < dt_now()):
        return render_template('error.html', error="Voting is closed"), 400

    songs = get_show_songs(show_data.year, show_data.short_name)

    errors = []

    username_invalid = False
    username = request.form['username']
    username = unicodedata.normalize('NFKC', username)
    username = username.strip()
    nickname = request.form['nickname']
    nickname = nickname.strip()
    country_id: Optional[str] = request.form['country']
    if not country_id:
        country_id = None

    if not username:
        username_invalid = True
        errors.append("Username is required.")

    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id FROM account WHERE LOWER(username) = LOWER(%s)', (username,))
    voter = cursor.fetchone()
    if voter:
        voter_id = voter['id']
    else:
        voter_id = 0

    country_codes = []
    country_names = []
    if voter_id:
        user_songs = get_user_songs(voter_id, show_data.year)
        countries = list(map(lambda s: s.country, user_songs))
        country_codes = [c.cc for c in countries]
        country_names = [c.name for c in countries]

    if country_codes and country_id not in country_codes:
        errors.append(f"You can only vote as one of the countries you submitted: ({', '.join(country_names)})")
        country_id = None

    cursor.execute('''
        SELECT id FROM song WHERE submitter_id = %s
    ''', (voter_id,))
    submitted_song = cursor.fetchone()
    if submitted_song:
        submitted_song = submitted_song['id']
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