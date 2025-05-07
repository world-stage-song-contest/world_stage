from collections import defaultdict
import sqlite3
from flask import render_template, request, redirect, url_for

from utils import get_show_id, points

def scoreboard(show: str):
    return render_template('scoreboard.html', show=show)

def scores(show: str):
    show_id, show_name, voting_opens, voting_closes, year = get_show_id(show)
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT song.id, song_show.running_order, country.name, song.title, song.artist FROM song
            JOIN song_show ON song.id = song_show.song_id
            JOIN country ON song.country_id = country.id
            WHERE song_show.show_id = ?
            ORDER BY song_show.running_order
        ''', (show_id,))
        songs = cursor.fetchall()

        cursor.execute('''
            SELECT song_id, vote.points, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN user ON vote_set.voter_id = user.id
            JOIN song ON vote.song_id = song.id
            ORDER BY vote_set.created_at
        ''')
        results_raw = cursor.fetchall()
        results = defaultdict(dict)
        vote_order = []
        for song_id, pts, username in results_raw:
            if username not in vote_order:
                vote_order.append(username)
            results[username][pts] = song_id

        cursor.execute('''
            SELECT username, nickname, country.name FROM vote_set
            JOIN user ON vote_set.voter_id = user.id
            JOIN country ON vote_set.country_id = country.id
            WHERE vote_set.show_id = ?
        ''', (show_id,))
        vote_set = cursor.fetchall()
        voter_assoc = {}
        for username, nickname, country in vote_set:
            voter_assoc[username] = {'nickname': nickname, 'country': country}

    return {'songs': songs, 'results': results, 'points': points, 'vote_order': vote_order, 'associations': voter_assoc}
