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
    access_type: str

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

def get_show_id(show: str, year: Optional[int] = None) -> Optional[ShowData]:
    if year:
        short_show_name = show
    else:
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
            SELECT show.id, show.point_system_id, show.show_name, show.voting_opens, show.voting_closes, show.dtf, show.sc, show.special, show.allow_access_type FROM show
            JOIN year ON show.year_id = year.id
            WHERE year.id = ? AND show.short_name = ?
        ''', (year, short_show_name))
    else:
        cursor.execute('''
            SELECT id, point_system_id, show_name, voting_opens, voting_closes, dtf, sc, special, allow_access_type FROM show
            WHERE short_name = ?
        ''', (short_show_name,))

    show_id = cursor.fetchone()
    if show_id:
        show_id, point_system_id, show_name, voting_opens, voting_closes, dtf, sc, special, access_type = show_id
    else:
        return None

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
        special=special,
        access_type=access_type
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
    total_votes: int
    max_pts: int
    show_voters: int
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
        if self.ro is None or other.ro is None:
            return False
        return self.ro < other.ro
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            return False
        return self.ro == other.ro and self.sum == other.sum and self.count == other.count and self.pts == other.pts
    
    def pct(self) -> str:
        if self.show_voters == 0 or self.max_pts == 0:
            return "0.00%"
        return f"{(self.sum / (self.show_voters * self.max_pts)) * 100:.2f}%"

    def get_pt(self, pt: int) -> int:
        if pt not in self.pts:
            return 0
        return self.pts[pt]

def get_votes_for_song(song_id: int, show_id: int, ro: int) -> VoteData:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT COUNT(*) FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        WHERE song_id = ? AND show_id = ?
    ''', (song_id, show_id))

    count = cursor.fetchone()[0]

    cursor.execute('''
        SELECT MAX(score) FROM show
        JOIN point ON show.point_system_id = point.point_system_id
        WHERE show.id = ?
    ''', (show_id,))
    max_pts = cursor.fetchone()[0]

    cursor.execute('''
        SELECT COUNT(DISTINCT voter_id) FROM vote_set
        WHERE show_id = ?
    ''', (show_id,))
    show_voters = cursor.fetchone()[0]
                   
    cursor.execute('''
        SELECT score FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN user ON vote_set.voter_id = user.id
        JOIN point ON vote.point_id = point.id
        WHERE song_id = ? AND show_id = ?
    ''', (song_id,show_id))
    res = VoteData(ro=ro, total_votes=count, max_pts=max_pts, show_voters=show_voters)
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
    

@total_ordering
@dataclass
class Song:
    id: int
    title: str
    artist: str
    cc: int
    country: str
    year: int
    placeholder: bool
    languages: list['Language']
    vote_data: Optional[VoteData]
    submitter: Optional[str]

    def __init__(self, id: int, title: str, artist: str, cc: int, country: str, year: int, placeholder: bool, submitter: Optional[str],
                 languages: list['Language'] = [], show_id: Optional[int] = None, ro: Optional[int] = None):
        self.id = id
        self.title = title
        self.artist = artist
        self.cc = cc
        self.country = country
        self.year = year
        self.languages = languages
        self.placeholder = placeholder
        self.submitter = submitter
        if show_id is not None and ro is not None:
            self.vote_data = get_votes_for_song(self.id, show_id, ro)
        else:
            self.vote_data = None

    def __lt__(self, other):
        if not isinstance(other, Song):
            return NotImplemented
        if self.vote_data is None or other.vote_data is None:
            return self.id < other.id
        else:
            return self.vote_data < other.vote_data
    
    def __eq__(self, other):
        if not isinstance(other, Song):
            return NotImplemented
        return self.id == other.id
    
    def as_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'artist': self.artist,
            'cc': self.cc,
            'country': self.country,
            'languages': self.languages,
        }
    
    def get_pt(self, points: int) -> Optional[int]:
        if self.vote_data is None:
            return None
        return self.vote_data.get_pt(points)

@dataclass
class Language:
    name: str
    tag: str
    extlang: str
    region: str
    subvariant: str
    suppress_script: bool

    def as_dict(self):
        return {
            'name': self.name,
            'tag': self.tag,
            'extlang': self.extlang,
            'region': self.region,
            'subvariant': self.subvariant,
            'suppress_script': self.suppress_script,
        }

@total_ordering
@dataclass
class Show:
    year: int
    short_name: str
    name: str

    def __lt__(self, other):
        def value_map(name: str) -> int:
            if name.startswith('sf'):
                return 0
            elif name == 'sc':
                return 1
            elif name == 'f':
                return 2
            else:
                return 3
            
        if not isinstance(other, Show):
            return NotImplemented
        
        if self.year != other.year:
            return self.year < other.year

        v1 = value_map(self.short_name)
        v2 = value_map(other.short_name)
        return v1 < v2

def get_song_languages(song_id: int) -> list[Language]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT language.name, language.tag, language.extlang, language.region, language.subvariant, language.suppress_script FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_id = ?
        ORDER BY priority
    ''', (song_id,))
    languages = [Language(lang[0], lang[1], lang[2], lang[3], lang[4], lang[5]) for lang in cursor.fetchall()]

    return languages

def get_show_songs(year: int, short_name: str, *, select_languages=False, select_votes=False) -> Optional[list[Song]]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT show.id FROM show
        JOIN year ON show.year_id = year.id
        WHERE year.id = ? AND show.short_name = ? AND allow_access_type = 'full'
        ''', (year, short_name))
    show_id = cursor.fetchone()
    if not show_id:
        return None
    show_id = show_id[0]

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.country_id, country.name, song.year_id, song_show.running_order, song.is_placeholder, user.username FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN show ON song_show.show_id = show.id
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN user on song.submitter_id = user.id
        WHERE show.id = ?
        ''', (show_id,))
    songs = [Song(id=song['id'],
                  title=song['title'],
                  artist=song['artist'],
                  cc=song['country_id'],
                  country=song['name'],
                  year=song['year_id'],
                  placeholder=bool(song['is_placeholder']),
                  submitter=song['username'],
                  ro=song['running_order'])
                for song in cursor.fetchall()]

    for song in songs:
        if select_languages:
            song.languages = get_song_languages(song.id)
        if select_votes:
            song.vote_data = get_votes_for_song(song.id, show_id, show_id)

    return songs

def get_year_winner(year: int) -> Optional[Song]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT closed FROM year
        WHERE id = ?
        ''', (year,))
    
    closed = cursor.fetchone()[0]
    if not closed:
        return None
    
    songs = get_show_songs(year, 'f', select_votes=True)
    if not songs:
        return None
    
    songs.sort(reverse=True)
    winner = songs[0]
    winner.languages = get_song_languages(winner.id)

    return winner

def get_year_songs(year: int, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.country_id, country.name, song.is_placeholder, user.username, song.year_id FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN user on song.submitter_id = user.id
        WHERE song.year_id = ?
        ORDER BY country.name
        ''', (year,))
    songs = [Song(id=song['id'],
                  title=song['title'],
                  artist=song['artist'],
                  cc=song['country_id'],
                  country=song['name'],
                  placeholder=bool(song['is_placeholder']),
                  year=song['year_id'],
                  submitter=song['username']) for song in cursor.fetchall()]

    if select_languages:
        for song in songs:
            song.languages = get_song_languages(song.id)

    return songs

def get_user_songs(user_id: int, year: Optional[int] = None, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    if year:
        cursor.execute('''
            SELECT song.id, song.title, song.artist, song.country_id, country.name, song.is_placeholder, user.username, song.year_id FROM song
            JOIN country ON song.country_id = country.id
            JOIN user on song.submitter_id = user.id
            WHERE song.submitter_id = ? AND song.year_id = ? AND song.year_id IS NOT NULL
            ORDER BY song.year_id, country.name
        ''', (user_id, year))
    else:
        cursor.execute('''
            SELECT song.id, song.title, song.artist, song.country_id, country.name, song.is_placeholder, user.username, song.year_id FROM song
            JOIN country ON song.country_id = country.id
            JOIN user on song.submitter_id = user.id
            WHERE song.submitter_id = ? AND song.year_id IS NOT NULL
            ORDER BY song.year_id, country.name
        ''', (user_id,))
    songs = [Song(id=song['id'],
                    title=song['title'],
                    artist=song['artist'],
                    cc=song['country_id'],
                    country=song['name'],
                    placeholder=bool(song['is_placeholder']),
                    year=song['year_id'],
                    submitter=song['username']) for song in cursor.fetchall()]

    if select_languages:
        for song in songs:
            song.languages = get_song_languages(song.id)
    return songs