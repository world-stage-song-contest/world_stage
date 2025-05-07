import datetime
import sqlite3

points = [20, 18, 16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

def format_timedelta(td: datetime.timedelta):
    days, seconds = td.days, td.seconds
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60

    values = []
    if days > 0:
        values.append(f"{days} days")
    if hours > 0:
        values.append(f"{hours} hours")
    if minutes > 0:
        values.append(f"{minutes} minutes")
    if seconds > 0:
        values.append(f"{seconds} seconds")
    return ', '.join(values)

def get_show_id(show):
    show_data = show.split('-')
    if len(show_data) == 2:
        year = int(show_data[0])
        short_show_name = show_data[1].upper()
    else:
        year = None
        short_show_name = show_data[0].upper()

    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id FROM year WHERE year = ?
        ''', (year,))
        year_id = cursor.fetchone()

        if year_id:
            year_id = year_id[0]

            cursor.execute('''
                SELECT id, show_name, voting_opens, voting_closes FROM show
                WHERE year_id = ? AND short_name = ?
            ''', (year_id, short_show_name))
            show_id = cursor.fetchone()
            if show_id:
                show_id, show_name, voting_opens, voting_closes = show_id
        else:
            cursor.execute('''
                SELECT id, show_name, voting_opens, voting_closes FROM show WHERE short_name = ?
            ''', (short_show_name,))
            show_id = cursor.fetchone()
            if show_id:
                show_id, show_name, voting_opens, voting_closes = show_id

    voting_opens = datetime.datetime.strptime(voting_opens, '%Y-%m-%d %H:%M')
    voting_closes = datetime.datetime.strptime(voting_closes, '%Y-%m-%d %H:%M')

    return show_id, show_name, voting_opens, voting_closes, year

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

def add_votes(username, nickname, show_id, votes):
    with sqlite3.connect('songs.db') as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO user (username) VALUES (?)', (username,))
        cursor.execute('SELECT id FROM user WHERE username = ?', (username,))
        voter_id = cursor.fetchone()[0]

        cursor.execute('SELECT id FROM vote_set WHERE voter_id = ? AND show_id = ?', (voter_id, show_id))
        existing_vote_set = cursor.fetchone()

        if not existing_vote_set:
            cursor.execute('''
                INSERT INTO vote_set (voter_id, show_id, nickname, created_at)
                VALUES (?, ?, ?, datetime('now'))
                ''', (voter_id, show_id, nickname))
            cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
            vote_set_id = cursor.fetchone()[0]
            for point, song_id in votes.items():
                cursor.execute('INSERT INTO vote (vote_set_id, song_id, points) VALUES (?, ?, ?)', (vote_set_id, song_id, point))
            return "added"
    
    update_votes(voter_id, nickname, votes)
    return "updated"