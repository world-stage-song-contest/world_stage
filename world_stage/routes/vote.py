from collections import defaultdict
from typing import Any, Optional
from flask import make_response, redirect, request, Blueprint, url_for
import datetime
import unicodedata

from ..utils import format_timedelta, get_show_id, get_countries, dt_now, get_show_songs, get_user_id_from_session, get_user_songs, get_vote_count_for_show, render_template
from ..db import get_db

bp = Blueprint('vote', __name__, url_prefix='/vote')

def update_votes(voter_id, nickname, country_id, point_system_id, votes, show_id) -> tuple[bool, str]:
    db = get_db()
    cursor = db.cursor()

    session_id = request.cookies.get('session')
    user_data = get_user_id_from_session(session_id)
    user_id = None
    if user_data:
        user_id = user_data[0]

    cursor.execute('SELECT id, ip_address FROM vote_set WHERE voter_id = %s AND show_id = %s', (voter_id, show_id))
    vote_set_data = cursor.fetchone()
    if not vote_set_data:
        return False, "Votes not found"

    vote_set_id = vote_set_data['id']
    ip_addr = vote_set_data['ip_address']

    if voter_id != user_id and ip_addr != request.remote_addr:
        return False, "IP addresses don't match. Log in or use the same device to vote."

    cursor.execute('''
        UPDATE vote_set SET nickname = %s, country_id = %s, ip_address = %s
        WHERE id = %s
    ''', (nickname, country_id or 'XX', request.remote_addr, vote_set_id))

    cursor.execute('UPDATE vote SET song_id = NULL WHERE vote_set_id = %s', (vote_set_id,))

    for score, song_id in votes.items():
        cursor.execute('''
            UPDATE vote
            SET song_id = %s
            WHERE vote_set_id = %s AND score = %s
        ''', (song_id, vote_set_id, score))

    return True, "updated"

def add_votes(username, nickname, country_id, show_id, point_system_id, votes) -> tuple[bool, str]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id FROM account WHERE username = %s', (username,))
    voter_id_data = cursor.fetchone()
    if not voter_id_data:
        return False, f"User with name '{username}' not found. Ensure that you entered your username into the Voter Name field and your display name into the Display Name field."
    voter_id = voter_id_data['id']

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = %s AND show_id = %s', (voter_id, show_id))
    existing_vote_set = cursor.fetchone()

    res = True
    if not existing_vote_set:
        cursor.execute('''
            INSERT INTO vote_set (voter_id, show_id, country_id, nickname, ip_address, created_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id
            ''', (voter_id, show_id, country_id or 'XX', nickname, request.remote_addr))
        vote_set_id = cursor.fetchone()['id'] # type: ignore
        for score, song_id in votes.items():
            cursor.execute('INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)', (vote_set_id, song_id, score))
        action = "added"
    else:
        res, action = update_votes(voter_id, nickname, country_id, point_system_id, votes, show_id)

    db.commit()

    return res, action

@bp.get('/')
def index():
    open_votings = []

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, show_name AS name, short_name, year_id AS year, voting_opens, voting_closes, predictions_close
        FROM show
        WHERE voting_opens <= CURRENT_TIMESTAMP
          AND (voting_closes IS NULL OR voting_closes >= CURRENT_TIMESTAMP)
        ORDER BY id
    ''')

    for row in cursor.fetchall():
        left = None
        if row['voting_closes']:
            left = row['voting_closes'] - dt_now()
        pred_deadline = row['predictions_close'] or row['voting_closes']
        predictions_open = not pred_deadline or pred_deadline >= dt_now()
        open_votings.append({
            'id': row['id'],
            'name': f"{row['year']} {row['name']}" if row['year'] else row['name'],
            'short_name': f"{row['year']}-{row['short_name']}" if row['year'] else row['short_name'],
            'voting_opens': row['voting_opens'],
            'voting_closes': row['voting_closes'],
            'predictions_open': predictions_open,
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

    if not session_id:
        return render_template('error.html', error="Please log in to vote"), 403

    selected: dict[int, dict[str, Any]] = defaultdict(dict)

    show_data = get_show_id(show)

    if not show_data or not show_data.id:
        return render_template('error.html', error="Show not found"), 404

    if (show_data.voting_opens and show_data.voting_opens > dt_now()
        or show_data.voting_closes and show_data.voting_closes < dt_now()):
        return render_template('error.html', error="Voting is closed"), 400

    db = get_db()
    cursor = db.cursor()

    d = get_user_id_from_session(session_id)
    if not d:
        return render_template('error.html', error="Unknown user ID"), 404

    _, username = d

    vote_set_id = None
    countries = []
    if username:
        cursor.execute('SELECT id FROM account WHERE username = %s', (username,))
        user_id = cursor.fetchone()
        if user_id:
            user_songs = get_user_songs(user_id['id'], show_data.year)
            countries = list(map(lambda s: s.country, user_songs))
            cursor.execute('''
                SELECT vote_set.id AS vsid, vote_set.nickname, vote_set.country_id AS cid
                FROM vote_set
                JOIN account ON vote_set.voter_id = account.id
                WHERE account.username = %s AND vote_set.show_id = %s
            ''', (username, show_data.id))
            vs_row = cursor.fetchone()
            if vs_row:
                vote_set_id = vs_row['vsid']
                nickname = vs_row['nickname']
                country_id = vs_row['cid']

    if not countries:
        countries = get_countries()

    if vote_set_id:
        cursor.execute('''
            SELECT song_id, score, country.id AS cc FROM vote
            JOIN song ON vote.song_id = song.id
            JOIN country ON song.country_id = country.id
            WHERE vote_set_id = %s
        ''', (vote_set_id,))
        for row in cursor.fetchall():
            selected[row['score']]['sid'] = row['song_id']
            selected[row['score']]['cc'] = row['cc']

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

    session_id = request.cookies.get('session')
    if not session_id:
        return render_template('error.html', error="Please log in to vote"), 403

    if not show_data or not show_data.id:
        return render_template('error.html', error="Show not found"), 404

    if (show_data.voting_opens and show_data.voting_opens > dt_now()
        or show_data.voting_closes and show_data.voting_closes < dt_now()):
        return render_template('error.html', error="Voting is closed"), 400

    songs = get_show_songs(show_data.year, show_data.short_name)

    errors = []

    d = get_user_id_from_session(session_id)
    if not d:
        return render_template('error.html', error="Unknown user ID"), 404

    voter_id, username = d

    nickname = request.form['nickname']
    nickname = nickname.strip()
    country_id: Optional[str] = request.form['country']
    if not country_id:
        country_id = None

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT 1
        FROM vote_set
        WHERE show_id = %s
          AND ip_address = %s
    ''', (show_data.id, request.remote_addr))

    #if cursor.fetchone():
    #    return render_template('error.html', error="A vote has already been entered from this IP address.")

    country_codes = []
    country_names = []

    user_songs = get_user_songs(voter_id, show_data.year)
    user_song_ids = [s.id for s in user_songs]
    countries = [s.country for s in user_songs]
    country_codes = [c.cc for c in countries]
    country_names = [c.name for c in countries]

    if country_codes and country_id not in country_codes:
        errors.append(f"You can only vote as one of the countries you submitted: ({', '.join(country_names)})")
        country_id = None

    missing = []
    for point in show_data.points:
        id_str = request.form.get(f'pts-{point}')
        if not id_str:
            missing.append(point)
            continue
        song_id = int(id_str)
        if song_id in user_song_ids:
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
        res, action = add_votes(username, nickname or None, country_id, show_data.id, show_data.point_system_id, votes)
        if res:
            resp = make_response(render_template('vote/success.html', action=action, what='vote', what_act='voting'))
            resp.set_cookie('username', username, max_age=datetime.timedelta(days=30))
            return resp
        else:
            return render_template('error.html', error=action)

    return render_template('vote/vote.html',
                           songs=songs, points=show_data.points, errors=errors,
                           selected=votes, invalid=invalid,
                           username=username, nickname=nickname,
                           year=show_data.year, show_name=show_data.name, show=show,
                           selected_country=country_id, countries=get_countries())


@bp.get('/<show>/predict')
def predict(show: str):
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    session_data = get_user_id_from_session(session_id)
    if not session_data:
        return redirect(url_for('session.login'))

    user_id, _ = session_data

    show_data = get_show_id(show)
    if not show_data or not show_data.id:
        return render_template('error.html', error="Show not found"), 404

    # Predictions close at predictions_close if set, otherwise fall back to voting_closes.
    pred_deadline = show_data.predictions_close or show_data.voting_closes
    if (show_data.voting_opens and show_data.voting_opens > dt_now()
            or pred_deadline and pred_deadline < dt_now()):
        return render_template('error.html', error="Predictions are closed for this show"), 400

    songs = get_show_songs(show_data.year, show_data.short_name)
    if not songs:
        return render_template('error.html', error="No songs found for this show"), 404

    db = get_db()
    cursor = db.cursor()

    # Load existing prediction and re-sort songs by previously predicted position
    cursor.execute('''
        SELECT id FROM prediction_set
        WHERE user_id = %s AND show_id = %s
    ''', (user_id, show_data.id))
    pred_set = cursor.fetchone()

    has_existing = False
    if pred_set:
        cursor.execute('''
            SELECT song_id, position FROM prediction
            WHERE set_id = %s
        ''', (pred_set['id'],))
        existing = {row['song_id']: row['position'] for row in cursor.fetchall()}
        if existing:
            has_existing = True
            songs = sorted(songs, key=lambda s: existing.get(s.id, len(songs) + 1)) # type: ignore

    cursor.execute('''
        SELECT COUNT(*) AS count FROM prediction_set WHERE show_id = %s
    ''', (show_data.id,))
    prediction_count = cursor.fetchone()['count'] # type: ignore

    return render_template('vote/predict.html',
                           songs=songs, show=show, show_name=show_data.name,
                           year=show_data.year, prediction_count=prediction_count,
                           has_existing=has_existing,
                           dtf=show_data.dtf or 0, sc=show_data.sc or 0)


@bp.post('/<show>/predict')
def predict_post(show: str):
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    session_data = get_user_id_from_session(session_id)
    if not session_data:
        return redirect(url_for('session.login'))

    user_id, _ = session_data

    show_data = get_show_id(show)
    if not show_data or not show_data.id:
        return render_template('error.html', error="Show not found"), 404

    pred_deadline = show_data.predictions_close or show_data.voting_closes
    if (show_data.voting_opens and show_data.voting_opens > dt_now()
            or pred_deadline and pred_deadline < dt_now()):
        return render_template('error.html', error="Predictions are closed for this show"), 400

    songs = get_show_songs(show_data.year, show_data.short_name)
    if not songs:
        return render_template('error.html', error="No songs found for this show"), 404

    valid_song_ids = {s.id for s in songs}
    n_songs = len(songs)

    # Parse form: field name = song id (int), field value = predicted position (int)
    data: list[dict] = []
    positions_seen: set[int] = set()
    errors: list[str] = []

    for key, value in request.form.items():
        try:
            song_id = int(key)
            position = int(value)
        except ValueError:
            continue  # skip submit button or any non-integer field

        if song_id not in valid_song_ids:
            errors.append(f"Unrecognised song (id {song_id}).")
            continue
        if position < 1 or position > n_songs:
            errors.append(f"Position {position} is out of range.")
            continue
        if position in positions_seen:
            errors.append(f"Duplicate position {position}.")
            continue

        positions_seen.add(position)
        data.append({'sid': song_id, 'pos': position})

    if len(data) != n_songs:
        errors.append(f"Expected {n_songs} predictions, got {len(data)}. Please rank every song.")

    if errors:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT COUNT(*) AS count FROM prediction_set WHERE show_id = %s', (show_data.id,))
        prediction_count = cursor.fetchone()['count'] # type: ignore
        return render_template('vote/predict.html',
                               songs=songs, show=show, show_name=show_data.name,
                               year=show_data.year, prediction_count=prediction_count,
                               has_existing=False, errors=errors,
                               dtf=show_data.dtf or 0, sc=show_data.sc or 0), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        INSERT INTO prediction_set (user_id, show_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, show_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING id
    ''', (user_id, show_data.id))
    prediction_set_id = cursor.fetchone()['id']  # type: ignore

    cursor.execute('DELETE FROM prediction WHERE set_id = %s', (prediction_set_id,))
    was_update = cursor.rowcount > 0

    cursor.executemany('''
        INSERT INTO prediction (set_id, song_id, position)
        VALUES (%(psid)s, %(sid)s, %(pos)s)
    ''', [{'psid': prediction_set_id, **item} for item in data])

    db.commit()

    action = 'updated' if was_update else 'submitted'
    return render_template('vote/success.html', action=action, what='prediction', what_act='predicting')
