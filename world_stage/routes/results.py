import datetime
import urllib.parse
import unicodedata
from flask import Blueprint, redirect, render_template, request, url_for
from collections import defaultdict

from ..db import get_db
from ..utils import get_show_id, SuspensefulVoteSequencer, dt_now, get_user_role_from_session, LCG, get_votes_for_song

bp = Blueprint('results', __name__, url_prefix='/results')

@bp.get('/')
def results_index():
    results = []
    db = get_db()
    cursor = db.cursor()
   
    cursor.execute('''
        SELECT show_name, short_name, year_id
        FROM show
        WHERE voting_closes < datetime('now')
        ORDER BY date
    ''')
    for name, short_name, year in cursor.fetchall():
        results.append({
            'name': f"{year} {name}" if year else name,
            'short_name': f"{year}-{short_name}" if year else short_name,
        })
    return render_template('results/index.html', results=results)

@bp.get('/<show>')
def results(show: str):
    show_data = get_show_id(show)

    def songs_comparer(a):
        pt_cnt = []
        for p in show_data.points:
            pt_cnt.append(a[p])
        val = (a['sum'], a['count']) + tuple(pt_cnt) + (-a['running_order'],)
        return val

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return render_template('error.html', error="Voting hasn't closed yet."), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, country.id, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    songs = []
    results = {}
    for id, ro, country, cc, title, artist in cursor.fetchall():
        val = defaultdict(int,
            id=id,
            running_order=ro,
            country=country,
            cc=cc,
            title=title,
            artist=artist
        )
        songs.append(val)
        results[id] = val

    for song_id in results.keys():
        cursor.execute('''
            SELECT score FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN point ON vote.point_id = point.id
            WHERE song_id = ? AND show_id = ?
        ''', (song_id, show_data.id))
        for pts, *_ in cursor.fetchall():
            results[song_id]['sum'] += pts
            results[song_id]['count'] += 1
            results[song_id][pts] += 1

    songs.sort(key=songs_comparer, reverse=True)

    return render_template('results/summary.html', songs=songs, points=show_data.points, show=show, show_name=show_data.name, show_id=show_data.id)

@bp.get('/<show>/detailed')
def detailed_results(show: str):
    show_data = get_show_id(show)

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, country.id, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    songs = []
    rs = {}
    for id, ro, country, cc, title, artist in cursor.fetchall():
        val = {
            'id': id,
            'running_order': ro,
            'country': country,
            'cc': cc,
            'title': title,
            'artist': artist,
            'sum': 0,
        }
        songs.append(val)
        rs[id] = val

    results = {}
    cursor.execute('''
        SELECT username, country_id, country.name FROM vote_set
        JOIN user ON vote_set.voter_id = user.id
        LEFT OUTER JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = ?
        ORDER BY created_at
    ''', (show_data.id,))
    for username, country_id, country_name in cursor.fetchall():
        results[username] = {'code': country_id or "XXX", 'country': country_name}

    for song in songs:
        song_id = song['id']
        cursor.execute('''
            SELECT point.score, song_id, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN user ON vote_set.voter_id = user.id
            JOIN point ON vote.point_id = point.id
            WHERE song_id = ? AND show_id = ?
            ORDER BY created_at
        ''', (song_id,show_data.id))

        for pts, song_id, username in cursor.fetchall():
            results[username][song_id] = pts
            rs[song_id]['sum'] += pts

    return render_template('results/detailed.html', songs=songs, results=results, show_name=show_data.name, show=show)

@bp.get('/<show>/scoreboard')
def scoreboard(show: str):
    show_data = get_show_id(show)

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    return render_template('results/scoreboard.html', show=show)

@bp.get('/<show>/scoreboard/votes')
def scores(show: str):
    show_data = get_show_id(show)

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return {'error': "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    songs = []
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, country.id, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    song_ids = []
    for id, running_order, country, cc, title, artist in cursor.fetchall():
        val = {
            'id': id,
            'ro': running_order,
            'country': country,
            'cc': cc,
            'title': title,
            'artist': artist,
        }
        songs.append(val)
        song_ids.append(id)

    cursor.execute('''
        SELECT song_id, point.score, username FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN user ON vote_set.voter_id = user.id
        JOIN song ON vote.song_id = song.id
        JOIN point ON vote.point_id = point.id
        WHERE vote_set.show_id = ?
        ORDER BY vote_set.created_at
    ''', (show_data.id,))
    results_raw = cursor.fetchall()
    results: dict[str, dict[int, int]] = defaultdict(dict)
    for song_id, pts, username in results_raw:
        results[username][pts] = song_id

    sequencer = SuspensefulVoteSequencer(results, song_ids, show_data.points)
    vote_order = sequencer.get_order()

    user_songs = defaultdict(list)
    for voter_username in vote_order:
        cursor.execute('''
            SELECT song.id FROM song
            JOIN user ON song.submitter_id = user.id
            JOIN song_show ON song.id = song_show.song_id
            WHERE user.username = ? AND song_show.show_id = ?
        ''', (voter_username,show_data.id))
        for song_id in cursor.fetchall():
            user_songs[voter_username].append(song_id[0])

    cursor.execute('''
        SELECT username, nickname, country_id, country.name FROM vote_set
        JOIN user ON vote_set.voter_id = user.id
        JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = ?
    ''', (show_data.id,))
    vote_set = cursor.fetchall()
    voter_assoc = {}
    for username, nickname, country_code, country_name in vote_set:
        voter_assoc[username] = {'nickname': nickname, 'country': country_name, 'code': country_code}

    return {'songs': songs, 'results': results, 'points': show_data.points, 'vote_order': vote_order, 'associations': voter_assoc, 'user_songs': user_songs}

@bp.get('/user/<username>')
def user_results(username: str):
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

    return render_template('results/user.html', votes=votes, username=username)

@bp.get('/<show>/qualifiers')
def qualifiers(show: str):
    show_data = get_show_id(show)

    if show_data.dtf is None:
        return render_template('error.html', error="Not a semi-final."), 400
    
    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    return render_template('results/qualifiers.html', show=show)

@bp.get('/<show>/qualifiers/votes')
def qualifiers_scores(show: str):
    show_data = get_show_id(show)

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400
    
    if show_data.access_type == 'none':
        return {"error": "This show is not public."}, 400

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return {'error': "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, country.id FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    countries = []
    for id, running_order, country, cc in cursor.fetchall():
        val = {
            'country': country,
            'cc': cc,
            'points': get_votes_for_song(id, show_data.id, running_order)
        }
        countries.append(val)

    countries.sort(key=lambda x: x['points'], reverse=True)

    dtf_countries = []
    for i in range(show_data.dtf):
        dtf_countries.append(countries[i])

    sc_countries = []
    for i in range(show_data.sc or 0):
        sc_countries.append(countries[show_data.dtf + i])

    countries.sort(key=lambda x: x['points'].ro)

    for c in countries:
        del c['points']

    lcg = LCG(show_data.id)
    lcg.shuffle(dtf_countries)
    lcg.shuffle(sc_countries)

    return {'countries': countries,'reveal_order': {'dtf': dtf_countries, 'sc': sc_countries},
            'dtf': show_data.dtf, 'sc': show_data.sc or 0, 'special': show_data.special or 0}