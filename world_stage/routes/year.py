from collections import defaultdict
from dataclasses import dataclass
from functools import total_ordering
from typing import Optional
from flask import render_template, request, Blueprint

from ..utils import LCG, Show, Song, SuspensefulVoteSequencer, VoteData, get_show_id, dt_now, get_user_role_from_session, get_votes_for_song, get_year_songs, get_year_winner
from ..db import get_db

bp = Blueprint('year', __name__, url_prefix='/year')

@bp.get('/')
def year_index():
    db = get_db()
    cursor = db.cursor()

    years = []
    upcoming = []

    cursor.execute('SELECT id, closed FROM year ORDER BY id DESC')
    for id, closed in cursor.fetchall():
        if closed:
            years.append({'id': id, 'closed': closed})
        else:
            upcoming.append({'id': id, 'closed': closed})

    upcoming.reverse()

    for year in years:
        year['winner'] = get_year_winner(year['id'])

    return render_template('year/index.html', years=years, upcoming=upcoming)

@bp.get('/<int:year>')
def year_view(year: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT closed FROM year WHERE id = ?', (year,))
    closed = cursor.fetchone()
    if not closed:
        return render_template('error.html', error='Year not closed yet'), 404

    songs = get_year_songs(year, select_languages=True)

    cursor.execute('SELECT short_name, show_name FROM show WHERE year_id = ?', (year,))
    shows = [Show(year, show[0], show[1]) for show in cursor.fetchall()]
    shows.sort()

    return render_template('year/year.html', year=year, songs=songs, closed=closed[0], shows=shows)

@bp.get('/<int:year>/<show>')
def results(year: int, show: str):
    show_data = get_show_id(show, year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

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
    for id, ro, country, cc, title, artist in cursor.fetchall():
        val = Song(id=id, title=title, artist=artist, cc=cc, country=country, placeholder=False, submitter=None, show_id=show_data.id, ro=ro, year=year)
        songs.append(val)

    songs.sort(reverse=True)

    return render_template('year/summary.html', songs=songs, points=show_data.points, show=show, show_name=show_data.name, show_id=show_data.id, year=year)

@bp.get('/<int:year>/<show>/detailed')
def detailed_results(year: int, show: str):
    show_data = get_show_id(show, year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

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
    songs: list[Song] = []
    for id, ro, country, cc, title, artist in cursor.fetchall():
        val = Song(id=id, title=title, artist=artist, cc=cc, country=country, placeholder=False, submitter=None, show_id=show_data.id, ro=ro, year=year)
        songs.append(val)

    results: dict = {}
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
        cursor.execute('''
            SELECT point.score, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN user ON vote_set.voter_id = user.id
            JOIN point ON vote.point_id = point.id
            WHERE song_id = ? AND show_id = ?
            ORDER BY created_at
        ''', (song.id, show_data.id))

        for pts, username in cursor.fetchall():
            results[username][song.id] = pts

    songs.sort(reverse=True)

    return render_template('year/detailed.html', songs=songs, results=results, show_name=show_data.name, show=show, year=year)

@bp.get('/<int:year>/<show>/scoreboard')
def scoreboard(year:int, show: str):
    show_data = get_show_id(show, year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404
    
    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    return render_template('year/scoreboard.html', show=show, year=year, show_name=show_data.name)

@bp.get('/<int:year>/<show>/scoreboard/votes')
def scores(year: int, show: str):
    show_data = get_show_id(show, year)

    if not show_data:
        return {"error": "Show not found"}, 404

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

@bp.get('/<int:year>/<show>/qualifiers')
def qualifiers(year: int, show: str):
    show_data = get_show_id(show, year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404
    
    if show_data.dtf is None:
        return render_template('error.html', error="Not a semi-final."), 400
    
    if show_data.access_type == 'none':
        return render_template('error.html', error="This show is not public."), 400

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.voting_closes > dt_now() and not permissions.can_view_restricted:
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    return render_template('year/qualifiers.html', show=show, year=year, show_name=show_data.name)

@bp.get('/<int:year>/<show>/qualifiers/votes')
def qualifiers_scores(year: int, show: str):
    show_data = get_show_id(show, year)

    if not show_data:
        return {"error": "Show not found"}, 404

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