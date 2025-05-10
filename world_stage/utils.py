import datetime
from typing import Optional
from .db import get_db
from dataclasses import dataclass
from copy import deepcopy

@dataclass
class ShowData:
    id: int
    points: list[int]
    point_system_id: int
    name: str
    voting_opens: datetime.datetime
    voting_closes: datetime.datetime
    year: Optional[int]

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

def get_show_id(show: str) -> ShowData:
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

    voting_opens = datetime.datetime.strptime(voting_opens, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc)
    voting_closes = datetime.datetime.strptime(voting_closes, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc)

    ret = ShowData(
        id=show_id,
        points=list(points),
        point_system_id=point_system_id,
        name=show_name,
        voting_opens=voting_opens,
        voting_closes=voting_closes,
        year=year
    )

    return ret

def get_points_for_system(point_system_id: int) -> list[int]:
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

def get_countries(only_participating: bool = False) -> list[dict]:
    if only_participating:
        query = 'SELECT id, name FROM country WHERE is_participating = 1 ORDER BY name'
    else:
        query = 'SELECT id, name FROM country ORDER BY name'
    db = get_db()
    cursor = db.cursor()

    cursor.execute(query)
    countries = []
    for id, name in cursor.fetchall():
        countries.append({
            'id': id,
            'name': name
        })
    return countries

def deterministic_shuffle(items: list, seed: int):
    n = len(items)

    def lcg(seed):
        a = 0x19660d
        c = 0x3c6ef35f
        m = 2**32
        while True:
            seed = (a * seed + c) % m
            yield seed

    rng = lcg(seed)

    for i in reversed(range(1, n)):
        j = next(rng) % (i + 1)
        items[i], items[j] = items[j], items[i]

def suspenseful_vote_order(vote_dict: dict[str, dict[int, int]], vote_items: list[int]) -> list[str]:
    remaining_votes = deepcopy(vote_dict)
    current_scores = {item: 0 for item in vote_items}
    
    order = []
    
    while remaining_votes:
        best_user = ''
        best_tension = float('inf')
        
        for user, vote in remaining_votes.items():
            temp_scores = current_scores.copy()
            for pts, item in vote.items():
                temp_scores[item] += pts

            sorted_scores = sorted(temp_scores.values(), reverse=True)
            tension = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0

            if tension < best_tension:
                best_tension = tension
                best_user = user

        for pts, item in remaining_votes[best_user].items():
            current_scores[item] += pts
        
        order.append(best_user)
        del remaining_votes[best_user]
    
    return order