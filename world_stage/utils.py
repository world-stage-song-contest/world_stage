from collections import defaultdict, deque
import datetime
from functools import total_ordering
from typing import Optional, Union
from .db import get_db
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Deque, TypeAlias
import urllib.parse

Bucket: TypeAlias = Deque[Tuple[str, Dict[int, int]]]

class LCG:
    def __init__(self, seed: int, a: int = 0x19660d, c: int = 0x3c6ef35f, m: int = 2**32) -> None:
        self.state = seed

    def next(self) -> int:
        self.state = (self.state * 48271) % 2147483647
        return self.state
    
    def shuffle(self, arr: list):
        n = len(arr)
        for i in range(n - 1, 0, -1):
            j = self.next() % (i + 1)
            arr[i], arr[j] = arr[j], arr[i]
    
    def sample[T](self, arr: list[T], k: int) -> list[T]:
        indices = list(range(len(arr)))
        self.shuffle(indices)
        return [arr[i] for i in indices[:k]]
    
    def lightly_shuffle[T](self, arr: list[T], num_swaps: int):
        n = len(arr)
        indices = self.sample(list(range(n-1)), 2*num_swaps)
        for i in range(0, len(indices), 2):
            a, b = indices[i], indices[i + 1]
            arr[a], arr[b] = arr[b], arr[a]

class SuspensefulVoteSequencer:
    def __init__(
        self,
        vote_dict: Dict[str, Dict[int, int]],
        vote_items: List[int],
        points: List[int],
        *,
        winner_weight: int = 2,
        swaps: int = 5,
        seed: int = 0
    ) -> None:
        self.vote_dict = vote_dict
        self.vote_items = vote_items
        self.points = sorted(points, reverse=True)
        self.winner_weight = winner_weight
        self.num_swaps = swaps
        self.seed = seed

        self.high_threshold, self.medium_threshold = self._calculate_thresholds()
        self.final_scores = self._calculate_final_scores()
        self.known_winner = max(self.final_scores, key=self.final_scores.__getitem__)

    def _calculate_thresholds(self) -> Tuple[int, int]:
        n = len(self.points)
        top = self.points[:n // 3]
        middle = self.points[n // 3: 2 * n // 3]
        high_threshold = min(top) if top else 0
        medium_threshold = min(middle) if middle else 0
        return high_threshold, medium_threshold

    def _calculate_final_scores(self) -> Dict[int, int]:
        scores: Dict[int, int] = {item: 0 for item in self.vote_items}
        for vote in self.vote_dict.values():
            for pts, item in vote.items():
                scores[item] += pts
        return scores

    def _classify_votes(self) -> Tuple[Bucket, Bucket, Bucket]:
        high: Bucket = deque()
        medium: Bucket = deque()
        low: Bucket = deque()

        for user, vote in self.vote_dict.items():
            winner_points = sum(pts for pts, item in vote.items() if item == self.known_winner)
            if winner_points >= self.high_threshold:
                high.append((user, vote))
            elif winner_points >= self.medium_threshold:
                medium.append((user, vote))
            else:
                low.append((user, vote))
        return low, medium, high

    def _suspense_metric(self, temp_scores: Dict[int, int], vote: Dict[int, int]) -> int:
        sorted_scores = sorted(temp_scores.values(), reverse=True)
        gap = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
        winner_points = sum(pts for pts, item in vote.items() if item == self.known_winner)
        return (winner_points * self.winner_weight) + gap

    def get_order(self) -> List[str]:
        low, medium, high = self._classify_votes()
        current_scores: Dict[int, int] = {item: 0 for item in self.vote_items}
        final_order: List[str] = []

        buckets: List[Bucket] = [low, medium, high]
        bucket_idx = 0

        while any(buckets):
            tried = 0
            while tried < len(buckets):
                bucket = buckets[bucket_idx % len(buckets)]
                bucket_idx += 1
                tried += 1

                if not bucket:
                    continue

                best_user: str = ""
                best_vote: Dict[int, int] = {}
                best_score: float = float('inf')

                for user, vote in bucket:
                    temp_scores = current_scores.copy()
                    for pts, item in vote.items():
                        temp_scores[item] += pts
                    score = self._suspense_metric(temp_scores, vote)
                    if score < best_score:
                        best_user, best_vote = user, vote
                        best_score = score

                for pts, item in best_vote.items():
                    current_scores[item] += pts
                final_order.append(best_user)

                # Remove used vote
                bucket = deque((u, v) for u, v in bucket if u != best_user)
                buckets[(bucket_idx - 1) % len(buckets)] = bucket
                break

        if self.num_swaps > 0:
            lcg = LCG(self.seed)
            lcg.lightly_shuffle(final_order, self.num_swaps)

        return final_order

@dataclass
class ShowData:
    id: int
    points: list[int]
    point_system_id: int
    name: str
    voting_opens: datetime.datetime
    voting_closes: datetime.datetime
    year: Optional[int]
    dtf: Optional[int]
    sc: Optional[int]
    special: Optional[int]

@dataclass
class UserPermissions:
    can_view_restricted: bool
    can_edit: bool
    can_approve: bool

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

def parse_timedelta(td: str) -> datetime.timedelta:
    """Parse a timedetla string in the format 'MM:SS' or 'HH:MM:SS'."""
    parts = list(map(int, td.split(':')))
    if len(parts) == 2:
        return datetime.timedelta(minutes=parts[0], seconds=parts[1])
    elif len(parts) == 3:
        return datetime.timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
    else:
        raise ValueError("Invalid timedelta format. Use 'MM:SS' or 'HH:MM:SS'.")

def dt_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)

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
            SELECT show.id, show.point_system_id, show.show_name, show.voting_opens, show.voting_closes, show.dtf, show.sc, show.special FROM show
            JOIN year ON show.year_id = year.id
            WHERE year.id = ? AND show.short_name = ?
        ''', (year, short_show_name))
    else:
        cursor.execute('''
            SELECT id, point_system_id, show_name, voting_opens, voting_closes, dtf, sc, special FROM show
            WHERE short_name = ?
        ''', (short_show_name,))

    show_id = cursor.fetchone()
    if show_id:
        show_id, point_system_id, show_name, voting_opens, voting_closes, dtf, sc, special = show_id

    points = get_points_for_system(point_system_id)

    ret = ShowData(
        id=show_id,
        points=list(points),
        point_system_id=point_system_id,
        name=show_name,
        voting_opens=voting_opens,
        voting_closes=voting_closes,
        year=year,
        dtf=dtf,
        sc=sc,
        special=special
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

def get_user_id_from_session(session_id: str | None) -> int | None:
    if not session_id:
        return None
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT user.id FROM session
        JOIN user ON session.user_id = user.id
        WHERE session.session_id = ? AND session.expires_at > datetime('now')
    ''', (session_id,))
    row = cursor.fetchone()
    if row:
        return row[0]
    return None

def get_user_role_from_session(session_id: str | None) -> UserPermissions:
    if not session_id:
        return UserPermissions(False, False, False)
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT role FROM session
        JOIN user ON session.user_id = user.id
        WHERE session.session_id = ? AND session.expires_at > datetime('now')
    ''', (session_id,))
    row = cursor.fetchone()
    if row:
        role = row[0]
        if role == 'admin':
            return UserPermissions(can_view_restricted=True, can_edit=True, can_approve=False)
        elif role == 'owner':
            return UserPermissions(can_view_restricted=True, can_edit=True, can_approve=True)
        elif role == 'editor':
            return UserPermissions(can_view_restricted=False, can_edit=True, can_approve=False)
        elif role == 'user':
            return UserPermissions(can_view_restricted=False, can_edit=False, can_approve=False)
    return UserPermissions(False, False, False)

def create_cookie(**kwargs: str) -> str:
    cookie = []
    for key, value in kwargs.items():
        value = urllib.parse.quote(value)
        cookie.append(f"{key}={value}")
    return '&'.join(cookie)

def parse_cookie(cookie: str) -> dict[str, str]:
    cookie_dict: dict[str, str] = defaultdict(str)
    if not cookie:
        return cookie_dict
    
    for item in cookie.split('&'):
        key, value = item.split('=')
        cookie_dict[key] = urllib.parse.unquote(value)
    return cookie_dict

@total_ordering
@dataclass
class VoteData:
    ro: int
    sum: int = 0
    count: int = 0
    pts: dict[int, int] = field(default_factory=lambda: defaultdict(int))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            raise TypeError("Cannot compare VoteData with non-VoteData object")
        if self.sum != other.sum:
            return self.sum < other.sum
        if self.count != other.count:
            return self.count < other.count
        this_keys = sorted(self.pts.keys())
        other_keys = sorted(other.pts.keys())
        this_max = max(this_keys)
        other_max = max(other_keys)
        if this_max != other_max:
            return this_max < other_max
        for i in range(len(this_keys)):
            if this_keys[i] != other_keys[i]:
                return this_keys[i] < other_keys[i]
        return self.ro < other.ro
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            return False
        return self.ro == other.ro and self.sum == other.sum and self.count == other.count and self.pts == other.pts

def get_votes_for_song(song_id: int, show_id: int, ro: int) -> VoteData:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT score FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN user ON vote_set.voter_id = user.id
        JOIN point ON vote.point_id = point.id
        WHERE song_id = ? AND show_id = ?
    ''', (song_id,show_id))
    res = VoteData(ro=ro)
    for points in cursor.fetchall():
        pt = points[0]
        res.sum += pt
        res.count += 1
        res.pts[pt] += 1
    return res

def format_seconds(seconds: int) -> str:
    """Format seconds into a string in the format MM:SS."""
    if seconds is None:
        return "00:00"
    if seconds < 0:
        return "00:00"
    minutes = seconds // 60
    seconds %= 60
    return f"{minutes:02}:{seconds:02}"

def parse_seconds(td: str | None) -> int | None:
    """Parse a string in the format MM:SS into seconds."""
    if not td:
        return None
    parts = list(map(int, td.split(':')))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    else:
        raise ValueError("Invalid time format. Use 'MM:SS'.")