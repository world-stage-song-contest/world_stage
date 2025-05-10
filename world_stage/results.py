import datetime
from typing import Union
from flask import Blueprint, redirect, render_template, request, url_for
from collections import defaultdict

from .db import get_db
from .utils import get_show_id, suspenseful_vote_order, deterministic_shuffle

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
    ''')
    for name, short_name, year in cursor.fetchall():
        results.append({
            'name': f"{year} {name}" if year else name,
            'short_name': short_name,
        })
    return render_template('results_index.html', results=results)

@bp.get('/<show>')
def results(show: str):
    show_data = get_show_id(show)

    override = request.args.get('override')

    def songs_comparer(a):
        pt_cnt = []
        for p in show_data.points:
            pt_cnt.append(a[p])
        val = (a['sum'], a['count']) + tuple(pt_cnt) + (-a['running_order'],)
        return val

    if override != "override" and show_data.voting_closes > datetime.datetime.now(datetime.timezone.utc):
        return render_template('error.html', error="Voting hasn't closed yet."), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    songs = []
    results = {}
    for id, ro, country, title, artist in cursor.fetchall():
        val = defaultdict(int,
            id=id,
            running_order=ro,
            country=country,
            title=title,
            artist=artist
        )
        songs.append(val)
        results[id] = val

    for song_id in results.keys():
        cursor.execute('''
            SELECT score FROM vote
            JOIN point ON vote.point_id = point.id
            WHERE song_id = ?
        ''', (song_id,))
        for pts, *_ in cursor.fetchall():
            results[song_id]['sum'] += pts
            results[song_id]['count'] += 1
            results[song_id][pts] += 1

    songs.sort(key=songs_comparer, reverse=True)

    return render_template('results.html', songs=songs, points=show_data.points, show=show, show_name=show_data.name, show_id=show_data.id)

@bp.get('/<show>/detailed')
def detailed_results(show: str):
    show_data = get_show_id(show)

    override = request.args.get('override')

    if override != "override" and show_data.voting_closes > datetime.datetime.now(datetime.timezone.utc):
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    songs = []
    rs = {}
    for id, ro, country, title, artist in cursor.fetchall():
        val = {
            'id': id,
            'running_order': ro,
            'country': country,
            'title': title,
            'artist': artist,
            'sum': 0,
        }
        songs.append(val)
        rs[id] = val

    results = {}
    cursor.execute('''
        SELECT DISTINCT username, country_id, country.name FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN user ON vote_set.voter_id = user.id
        JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = ?
    ''', (show_data.id,))
    for username, country_id, country_name in cursor.fetchall():
        results[username] = {'code': country_id or "XRW", 'country': country_name}

    for song in songs:
        song_id = song['id']
        cursor.execute('''
            SELECT point.score, song_id, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN user ON vote_set.voter_id = user.id
            JOIN point ON vote.point_id = point.id
            WHERE song_id = ?
            ORDER BY created_at
        ''', (song_id,))

        for pts, song_id, username in cursor.fetchall():
            results[username][song_id] = pts
            rs[song_id]['sum'] += pts

    return render_template('detailed_votes.html', songs=songs, results=results, show_name=show_data.name, show=show)

@bp.get('/<show>/scoreboard')
def scoreboard(show: str):
    show_data = get_show_id(show)

    override = request.args.get('override')

    if override != "override" and show_data.voting_closes > datetime.datetime.now(datetime.timezone.utc):
        return render_template('error.html', error="Voting hasn't closed yet."), 400
    
    return render_template('scoreboard.html', show=show)

@bp.get('/<show>/scoreboard/votes')
def scores(show: str):
    show_data = get_show_id(show)

    override = request.args.get('override')

    if override != "override" and show_data.voting_closes > datetime.datetime.now(datetime.timezone.utc):
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
            'code': cc,
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
        ORDER BY vote_set.created_at
    ''')
    results_raw = cursor.fetchall()
    results: dict[str, dict[int, int]] = defaultdict(dict)
    alt_vote_order = []
    for song_id, pts, username in results_raw:
        if username not in alt_vote_order:
            alt_vote_order.append(username)
        results[username][pts] = song_id

    vote_order = suspenseful_vote_order(results, song_ids)

    if len(vote_order) != len(results):
        print("\033[31mVote order is not the same length as results. This is a problem.\033[0m")
        vote_order = alt_vote_order
        deterministic_shuffle(alt_vote_order, show_data.id)

    user_songs = {}
    for voter_username in vote_order:
        cursor.execute('''
            SELECT song.id FROM song
            JOIN user ON song.submitter_id = user.id
            JOIN song_show ON song.id = song_show.song_id
            WHERE user.username = ? AND song_show.show_id = ?
        ''', (voter_username,show_data.id))
        for song_id in cursor.fetchall():
            user_songs[voter_username] = list(song_id)

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
