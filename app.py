from flask import Flask, render_template, request, redirect, send_file, url_for
import sqlite3
from collections import defaultdict

app = Flask(__name__)

points = [20, 18, 16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
songs = []

def create_db():
    conn = sqlite3.connect('songs.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS song (
            id INTEGER PRIMARY KEY,
            country TEXT,
            title TEXT,
            artist TEXT,
            submitter TEXT,
            running_order INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS voter (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            UNIQUE(username)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vote_set (
            id INTEGER PRIMARY KEY,
            voter_id INTEGER,
            nickname TEXT,
            country TEXT,
            created_at TEXT,
            FOREIGN KEY (voter_id) REFERENCES voter (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vote (
            id INTEGER PRIMARY KEY,
            vote_set_id INTEGER,
            song_id INTEGER,
            points INTEGER,
            FOREIGN KEY (vote_set_id) REFERENCES vote_set (id) ON DELETE CASCADE,
            FOREIGN KEY (song_id) REFERENCES song (id)
        )
    ''')
    conn.commit()
    conn.close()

def update_votes(voter_id, nickname, votes):
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
        vote_set_id = cursor.fetchone()[0]

        cursor.execute('UPDATE vote_set SET nickname = ? WHERE id = ?', (nickname, vote_set_id))

        for point, song_id in votes.items():
            cursor.execute('''
                UPDATE vote
                SET points = ?
                WHERE vote_set_id = ? AND song_id = ?
            ''', (point, vote_set_id, song_id))

def add_votes(username, nickname, votes):
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO voter (username) VALUES (?)', (username,))
        cursor.execute('SELECT id FROM voter WHERE username = ?', (username,))
        voter_id = cursor.fetchone()[0]

        cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
        existing_vote_set = cursor.fetchone()

        if existing_vote_set:
            update_votes(voter_id, nickname, votes)
            return "updated"

        cursor.execute('INSERT OR IGNORE INTO vote_set (voter_id, nickname) VALUES (?, ?)', (voter_id,nickname))
        cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
        vote_set_id = cursor.fetchone()[0]
        for point, song_id in votes.items():
            cursor.execute('INSERT INTO vote (vote_set_id, song_id, points) VALUES (?, ?, ?)', (vote_set_id, song_id, point))

        return "added"

@app.get('/')
def home():
    return render_template('home.html')

@app.get('/success')
def success():
    action = request.args.get('action')
    return render_template('successfully_voted.html', action=action)

@app.get('/favicon.ico')
def favicon():
    return send_file('files/favicon.ico')

@app.route('/vote', methods=['GET', 'POST'])
def index():
    votes = {}
    invalid = []
    username = ''
    nickname = ''
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM song')
        songs = cursor.fetchall()

        errors = []

        if request.method == 'POST':
            username = request.form['username']

            if not username:
                errors.append("Username is required.")

            cursor.execute('''
                SELECT id FROM song WHERE submitter = ?
            ''', (username,))
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
                action = add_votes(username, nickname, votes)
                return redirect(url_for('success', action=action))

    return render_template('songs.html',
                           songs=songs, points=points, errors=errors,
                           selected=votes, invalid=invalid,
                           username=username, nickname=nickname)

@app.route('/scoreboard')
def scoreboard():
    return render_template('scoreboard.html')

@app.route('/scoreboard/votes')
def scores():
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, running_order, country, title, artist FROM song
        ''')
        songs = cursor.fetchall()

        cursor.execute('''
            SELECT song_id, vote.points, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN voter ON vote_set.voter_id = voter.id
            JOIN song ON vote.song_id = song.id
        ''')
        results_raw = cursor.fetchall()
        results = defaultdict(dict)
        vote_order = []
        for song_id, pts, username in results_raw:
            if username not in vote_order:
                vote_order.append(username)
            results[username][pts] = song_id

        cursor.execute('''
            SELECT username, nickname, country FROM vote_set
            JOIN voter ON vote_set.voter_id = voter.id
        ''')
        vote_set = cursor.fetchall()
        voter_assoc = {}
        for username, nickname, country in vote_set:
            voter_assoc[username] = {'nickname': nickname, 'country': country}

    return {'songs': songs, 'results': results, 'points': points, 'vote_order': vote_order, 'associations': voter_assoc}

@app.route('/results')
def results():
    def songs_comparer(a):
        pt_cnt = []
        for p in points:
            pt_cnt.append(a[p])
        val = (a['sum'], a['count']) + tuple(pt_cnt) + (-a['running_order'],)
        return val

    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, running_order, country, title, artist FROM song
        ''')
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

@app.route('/results/detailed')
def detailed_results():
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, running_order, country, title, artist FROM song
            ORDER BY running_order
        ''')
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
                JOIN voter ON vote_set.voter_id = voter.id
                WHERE song_id = ?
                ORDER BY created_at
            ''', (song_id,))

            for pts, song_id, username in cursor.fetchall():
                results[username][song_id] = pts
                rs[song_id]['sum'] += pts

    print(results)

    return render_template('user_votes.html', songs=songs, results=results)

if __name__ == '__main__':
    app.run(debug=True, port=8000)