import datetime
from flask import redirect, render_template, url_for
from collections import defaultdict
import sqlite3

from utils import get_show_id, points

def results_index():
    results = []
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT show_name, short_name, year.year
            FROM show
            LEFT OUTER JOIN year ON show.year_id = year.id
            WHERE show.voting_closes > datetime('now')
        ''')
        for name, short_name, year in cursor.fetchall():
            results.append({
                'name': f"{year} {name}" if year else name,
                'short_name': short_name,
            })
    return render_template('results_index.html', results=results)

def results(show: str):
    def songs_comparer(a):
        pt_cnt = []
        for p in points:
            pt_cnt.append(a[p])
        val = (a['sum'], a['count']) + tuple(pt_cnt) + (-a['running_order'],)
        return val

    show_id, show_name, voting_opens, voting_closes, year = get_show_id(show)

    if voting_closes < datetime.datetime.now():
        return redirect(url_for('error', error="Voting hasn't closed yet."))

    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
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
                SELECT vote.points FROM vote
                WHERE song_id = ?
            ''', (song_id,))
            for pts, *_ in cursor.fetchall():
                results[song_id]['sum'] += pts
                results[song_id]['count'] += 1
                results[song_id][pts] += 1

    songs.sort(key=songs_comparer, reverse=True)

    return render_template('results.html', songs=songs, points=points)

def detailed_results(show: str):
    show_id, show_name, voting_opens, voting_closes, year = get_show_id(show)

    if voting_closes < datetime.datetime.now():
        return redirect(url_for('error', error="Voting hasn't closed yet."))
    
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
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
                SELECT vote.points, song_id, username FROM vote
                JOIN vote_set ON vote.vote_set_id = vote_set.id
                JOIN user ON vote_set.voter_id = user.id
                WHERE song_id = ?
                ORDER BY created_at
            ''', (song_id,))

            for pts, song_id, username in cursor.fetchall():
                results[username][song_id] = pts
                rs[song_id]['sum'] += pts

    print(results)

    return render_template('user_votes.html', songs=songs, results=results)