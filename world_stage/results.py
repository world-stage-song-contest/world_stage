import datetime
from flask import Blueprint, redirect, render_template, request, url_for
from collections import defaultdict
import random

from .db import get_db
from .utils import deterministic_shuffle, get_show_id

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
    show_id = show_data['id']
    voting_closes = show_data['voting_closes']
    points = show_data['points']

    override = request.args.get('override')

    def songs_comparer(a):
        pt_cnt = []
        for p in points:
            pt_cnt.append(a[p])
        val = (a['sum'], a['count']) + tuple(pt_cnt) + (-a['running_order'],)
        return val

    if override != "override" and voting_closes > datetime.datetime.now(datetime.timezone.utc):
        return redirect(url_for('main.error', error="Voting hasn't closed yet."))

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_id,))
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

    return render_template('results.html', songs=songs, points=points)

@bp.get('/<show>/detailed')
def detailed_results(show: str):
    show_data = get_show_id(show)
    show_id = show_data['id']
    voting_closes = show_data['voting_closes']

    override = request.args.get('override')

    if override != "override" and voting_closes > datetime.datetime.now(datetime.timezone.utc):
        return redirect(url_for('main.error', error="Voting hasn't closed yet."))
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_id,))
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

    results = defaultdict(dict)
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

    return render_template('user_votes.html', songs=songs, results=results)

@bp.get('/<show>/scoreboard')
def scoreboard(show: str):
    show_data = get_show_id(show)
    voting_closes = show_data['voting_closes']

    override = request.args.get('override')

    if override != "override" and voting_closes > datetime.datetime.now(datetime.timezone.utc):
        pass#return redirect(url_for('main.error', error="Voting hasn't closed yet."))
    
    return render_template('scoreboard.html', show=show)

@bp.get('/<show>/scoreboard/votes')
def scores(show: str):
    show_data = get_show_id(show)
    show_id = show_data['id']
    points = show_data['points']
    voting_closes = show_data['voting_closes']

    override = request.args.get('override')

    if override != "override" and voting_closes > datetime.datetime.now(datetime.timezone.utc):
        return redirect(url_for('main.error_json', error="Voting hasn't closed yet."))

    db = get_db()
    cursor = db.cursor()
    songs = []
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name, country.id, song.title, song.artist FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = ?
        ORDER BY song_show.running_order
    ''', (show_id,))
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

    cursor.execute('''
        SELECT song_id, point.score, username FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN user ON vote_set.voter_id = user.id
        JOIN song ON vote.song_id = song.id
        JOIN point ON vote.point_id = point.id
        ORDER BY vote_set.created_at
    ''')
    results_raw = cursor.fetchall()
    results = defaultdict(dict)
    vote_order = []
    for song_id, pts, username in results_raw:
        if username not in vote_order:
            vote_order.append(username)
        results[username][pts] = song_id

    deterministic_shuffle(vote_order, show_id)

    cursor.execute('''
        SELECT username, nickname, country_id, country.name FROM vote_set
        JOIN user ON vote_set.voter_id = user.id
        JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = ?
    ''', (show_id,))
    vote_set = cursor.fetchall()
    voter_assoc = {}
    for username, nickname, country_code, country_name in vote_set:
        voter_assoc[username] = {'nickname': nickname, 'country': country_name, 'code': country_code}

    return {'songs': songs, 'results': results, 'points': points, 'vote_order': vote_order, 'associations': voter_assoc}
