import datetime
from .db import get_db

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
    ret = {
        'id': None,
        'points': None,
        'point_system_id': None,
        'show_name': None,
        'voting_opens': None,
        'voting_closes': None,
        'year': None
    }
    show_data = show.split('-')
    if len(show_data) == 2:
        year = int(show_data[0])
        short_show_name = show_data[1]
    else:
        year = None
        short_show_name = show_data[0]

    db = get_db()
    cursor = db.cursor()

    if year:
        year_id = year_id[0]

        cursor.execute('''
            SELECT show.id, show.point_system_id, show.show_name, show.voting_opens, show.voting_closes FROM show
            JOIN year ON show.year_id = year.id
            WHERE year.year = ? AND show.short_name = ?
        ''', (year, short_show_name))
        show_id = cursor.fetchone()
        if show_id:
            show_id, point_system_id, show_name, voting_opens, voting_closes = show_id
    else:
        cursor.execute('''
            SELECT id, point_system_id, show_name, voting_opens, voting_closes FROM show
            WHERE short_name = ?
        ''', (short_show_name,))
        show_id = cursor.fetchone()
        if show_id:
            show_id, point_system_id, show_name, voting_opens, voting_closes = show_id

    points = get_points_for_system(point_system_id)

    voting_opens = datetime.datetime.strptime(voting_opens, '%Y-%m-%d %H:%M')
    voting_closes = datetime.datetime.strptime(voting_closes, '%Y-%m-%d %H:%M')

    ret['id'] = show_id
    ret['point_system_id'] = point_system_id
    ret['show_name'] = show_name
    ret['voting_opens'] = voting_opens
    ret['voting_closes'] = voting_closes
    ret['year'] = year
    ret['points'] = list(points)

    return ret

def get_points_for_system(point_system_id):
    db = get_db()
    cursor = db.cursor()

    points = []
    cursor.execute('''
        SELECT score FROM point
        WHERE point_system_id = ?
        ORDER BY place
    ''', (point_system_id,))
    for p in cursor.fetchall():
        points.append(p[0])

    return points

def update_votes(voter_id, nickname, point_system_id, votes):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id FROM vote_set WHERE voter_id = ?', (voter_id,))
    vote_set_id = cursor.fetchone()[0]

    cursor.execute('UPDATE vote_set SET nickname = ? WHERE id = ?', (nickname, vote_set_id))

    for point, song_id in votes.items():
        cursor.execute('''
            SELECT id FROM point 
            WHERE point_system_id = ? AND score = ?
            ''', (point_system_id, point))
        point_id = cursor.fetchone()[0]
        cursor.execute('''
            UPDATE vote
            SET song_id = ?
            WHERE vote_set_id = ? AND point_id = ?
        ''', (song_id, vote_set_id, point_id))

def add_votes(username, nickname, show_id, point_system_id, votes):
    db = get_db()
    cursor = db.cursor()

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
            cursor.execute('''
                SELECT id FROM point 
                WHERE point_system_id = ? AND score = ?
                ''', (point_system_id, point))
            point_id = cursor.fetchone()[0]
            cursor.execute('INSERT INTO vote (vote_set_id, song_id, point_id) VALUES (?, ?, ?)', (vote_set_id, song_id, point_id))
        action = "added"
    else:
        update_votes(voter_id, nickname, point_system_id, votes)
        action = "updated"
    
    db.commit()

    return action