from abc import ABC, abstractmethod
from collections import defaultdict, deque
import datetime
from enum import Enum
from functools import total_ordering
import json
from typing import Any, Optional

from flask import Response, request, url_for
import flask

from .db import fetchone, get_db
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple, Deque, TypeAlias
import urllib.parse

from functools import lru_cache
from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
import re

Bucket: TypeAlias = Deque[Tuple[str, Dict[int, int]]]

class LCG:
    def __init__(self, seed: int, a: int = 0x19660d, c: int = 0x3c6ef35f, m: int = 2**32) -> None:
        self.state = seed
        self.a = a
        self.c = c
        self.m = m
        self.seed = seed

    def next(self, limit: int | None) -> int:
        self.state = (self.state * self.a + self.c) % self.m
        if limit:
            return self.state % limit
        else:
            return self.state

    def shuffle(self, arr: list):
        n = len(arr)
        for i in range(n - 1, 0, -1):
            j = self.next(i + 1)
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

class AbstractVoteSequencer(ABC):
    def __init__(
        self,
        vote_dict: Dict[str, Dict[int, int]],
        vote_items: List['Song'],
        points: List[int],
        *,
        winner_weight: int = 2,
        swaps: int = 5,
        seed: int = 1
    ) -> None:
        self.vote_dict = vote_dict
        self.vote_items = vote_items
        self.points = sorted(points, reverse=True)
        self.winner_weight = winner_weight
        self.num_swaps = swaps
        self.seed = seed
        self.first_half = len(vote_dict) // 3 * 2

        self.song_ids = [song.id for song in vote_items]
        self.submitter_by_song = {song.id: song.submitter for song in vote_items}
        self.song_by_submitter = {song.submitter: song.id for song in vote_items}

        self.high_threshold, self.medium_threshold = self._calculate_thresholds()
        self.final_scores = self._calculate_final_scores()
        self.known_winner = max(self.final_scores, key=self.final_scores.__getitem__)
        self.top_entries = self._get_top_entries(n=3)

    def _calculate_thresholds(self) -> Tuple[int, int]:
        n = len(self.points)
        top = self.points[:n // 3]
        middle = self.points[n // 3: 2 * n // 3]
        high_threshold = min(top) if top else 0
        medium_threshold = min(middle) if middle else 0
        return high_threshold, medium_threshold

    def _calculate_final_scores(self) -> Dict[int, int]:
        scores: Dict[int, int] = {(item.id): 0 for item in self.vote_items}
        for vote in self.vote_dict.values():
            for pts, item in vote.items():
                scores[item] += pts
        return scores

    def _get_top_entries(self, n: int) -> List[int]:
        sorted_items = sorted(self.final_scores.items(), key=lambda x: x[1], reverse=True)
        return [item for item, _ in sorted_items[:n]]

    def _classify_votes(self) -> Tuple[Bucket, Bucket, Bucket, List[str]]:
        high: Bucket = deque()
        medium: Bucket = deque()
        low: Bucket = deque()
        early_voters: List[str] = []

        top_submitters = {
            self.submitter_by_song[song_id]
            for song_id in self.top_entries
            if song_id in self.submitter_by_song
        }

        for user, vote in self.vote_dict.items():
            if user in top_submitters:
                early_voters.append(user)
                continue

            winner_points = sum(pts for pts, item in vote.items() if item == self.known_winner)
            if winner_points >= self.high_threshold:
                high.append((user, vote))
            elif winner_points >= self.medium_threshold:
                medium.append((user, vote))
            else:
                low.append((user, vote))

        return low, medium, high, early_voters

    def _suspense_metric(self, temp_scores: Dict[int, int], vote: Dict[int, int]) -> int:
        sorted_scores = sorted(temp_scores.values(), reverse=True)
        gap = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
        winner_points = sum(pts for pts, item in vote.items() if item == self.known_winner)
        return (winner_points * self.winner_weight) + gap

    @abstractmethod
    def get_order(self) -> List[str]:
        pass

class SuspensefulVoteSequencer(AbstractVoteSequencer):
    def get_order(self) -> List[str]:
        low, medium, high, early_voters = self._classify_votes()
        current_scores: Dict[int, int] = {song_id: 0 for song_id in self.song_ids}
        final_order: List[str] = []
        lcg = LCG(self.seed)

        buckets: List[Bucket] = [low, medium, high]
        bucket_idx = 0
        remaining_voters = set(self.vote_dict.keys()) - set(final_order)

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
                    if user not in remaining_voters:
                        continue
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
                remaining_voters.remove(best_user)

                # Remove from bucket
                bucket = deque((u, v) for u, v in bucket if u != best_user)
                buckets[(bucket_idx - 1) % len(buckets)] = bucket
                break

        for v in early_voters:
            num = lcg.next(self.first_half)
            final_order.insert(num, v)

        return final_order

class RandomVoteSequencer(AbstractVoteSequencer):
    def get_order(self) -> List[str]:
        _, _, _, early_voters = self._classify_votes()
        final_order_set = set(self.vote_dict.keys())

        for v in early_voters:
            final_order_set.remove(v)

        final_order = list(final_order_set)

        lcg = LCG(self.seed)

        lcg.shuffle(final_order)

        for v in early_voters:
            num = lcg.next(self.first_half)
            final_order.insert(num, v)

        return final_order

class ChronologicalVoteSequencer(AbstractVoteSequencer):
    def get_order(self) -> List[str]:
        return list(self.vote_dict.keys())

@dataclass
class ShowData:
    id: int
    points: list[int]
    point_system_id: int
    name: str
    short_name: str
    voting_opens: datetime.datetime | None
    voting_closes: datetime.datetime | None
    predictions_close: datetime.datetime | None
    year: int | None
    dtf: int | None
    sc: int | None
    special: int | None
    status: str

@dataclass(frozen=True)
class UserPermissions:
    role: str = 'none'
    can_edit: bool = False
    can_view_restricted: bool = False

    def __str__(self) -> str:
        return self.role

@dataclass(kw_only=True)
class Country:
    cc: str
    name: str
    is_participating: bool
    cc3: str

@total_ordering
@dataclass
class VoteData:
    ro: int
    total_votes: int | None
    max_pts: int | None
    show_voters: int | None
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
        this_max = max(this_keys, default=0)
        other_max = max(other_keys, default=0)
        if this_max != other_max:
            return this_max < other_max
        overall_max = max(this_max, other_max)
        for i in range(overall_max,0,-1):
            this_v = self.pts.get(i, 0)
            other_v = other.pts.get(i, 0)
            if this_v != other_v:
                return this_v < other_v
        if self.ro is None or other.ro is None:
            return False
        return self.ro > other.ro

    def __str__(self):
        return f"VoteData(ro={self.ro}, count={self.count}, pts={self.pts})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            return False
        return self.ro == other.ro and self.sum == other.sum and self.count == other.count and self.pts == other.pts

    def pct(self) -> str:
        if not self.show_voters or not self.max_pts:
            return "0.00%"
        return f"{(self.sum / (self.show_voters * self.max_pts)) * 100:.2f}%"

    def get_pt(self, pt: int) -> int:
        if pt not in self.pts:
            return 0
        return self.pts[pt]

    def as_dict(self) -> dict:
        return {
            'ro': self.ro,
            'total_votes': self.total_votes,
            'max_pts': self.max_pts,
            'show_voters': self.show_voters,
            'sum': self.sum,
            'count': self.count,
            'pts': dict(self.pts)
        }

@dataclass(frozen=True)
class Language:
    name: str = ''
    tag: str = ''
    extlang: str | None = None
    region: str | None = None
    subvariant: str | None = None
    suppress_script: str | None = None

    @lru_cache
    def str(self, script: str | None = None, cc: str | None = None) -> str:
        components = [self.tag]
        if self.extlang:
            components.append(self.extlang)
        if script and script != self.suppress_script:
            components.append(script)
        if cc:
            components.append(cc.upper())
        if self.subvariant:
            components.append(self.subvariant)
        return '-'.join(components)

    def as_dict(self):
        return {
            'name': self.name,
            'tag': self.tag,
            'extlang': self.extlang,
            'region': self.region,
            'subvariant': self.subvariant,
            'suppress_script': self.suppress_script,
        }

class RawSongData(dict):
    def __init__(self, data=None, **kwargs):
        if data is None:
            data = {}
        super().__init__(data, **kwargs)

class CachedSongData(dict):
    def __init__(self, data=None, **kwargs):
        if data is None:
            data = {}
        super().__init__(data, **kwargs)

@total_ordering
@dataclass
class Song:
    id: int
    title: str
    artist: str
    country: Country
    year: int | None
    placeholder: bool
    languages: list[Language]
    vote_data: VoteData | None
    submitter: str | None
    submitter_id: int | None
    native_title: str | None
    title_lang: Language | None
    native_lang: Language | None
    translated_lyrics: str | None
    latin_lyrics: str | None
    native_lyrics: str | None
    lyrics_notes: str | None
    video_link: str | None
    poster_link: str | None
    recap_start: str | None
    recap_end: str | None
    sources: str | None
    hidden: bool = False

    def __init__(self, song: RawSongData | CachedSongData):
        if isinstance(song, RawSongData):
            self._RawSongData_init(song)
        elif isinstance(song, CachedSongData):
            pass
        else:
            raise TypeError(f'Bad type: {type(song)}')

    def _RawSongData_init(self, song: RawSongData):
        self._raw_init(
            id=song['id'],
            title=song['title'],
            native_title=song['native_title'],
            artist=song['artist'],
            video_link=song['video_link'],
            poster_link=song['poster_link'],
            recap_start=song['snippet_start'],
            recap_end=song['snippet_end'],
            country=Country(cc=song['country_id'],
                            name=song['name'],
                            is_participating=bool(song['is_participating']),
                            cc3=song['cc3']),
            placeholder=bool(song['is_placeholder']),
            year=song['year_id'],
            title_lang=song['title_language_id'],
            submitter_id=song['submitter_id'],
            native_lang=song['native_language_id'],
            translated_lyrics=song['translated_lyrics'],
            latin_lyrics=song['romanized_lyrics'],
            native_lyrics=song['native_lyrics'],
            lyrics_notes=song['notes'],
            sources=song['sources'],
            submitter=song['username'],
            show_id=song.get('show_id'),
            ro=song.get('running_order')
        )

    def _raw_init(self, *,
                 id: int, title: str, native_title: str | None, artist: str,
                 country: Country, year: int | None, poster_link: str | None,
                 placeholder: bool, submitter: str | None, submitter_id: int | None,
                 title_lang: int | None, native_lang: int | None, lyrics_notes: str | None,
                 translated_lyrics: str | None, latin_lyrics: str | None, native_lyrics: str | None,
                 video_link: str | None, recap_start: int | None, recap_end: int | None,
                 sources: str | None,
                 languages: list['Language'] = [], show_id: int | None = None, ro: int | None = None):
        self.id = id
        self.title = title
        self.artist = artist
        self.country = country
        self.native_title = native_title
        self.year = year
        self.languages = languages
        self.placeholder = placeholder
        self.submitter = submitter
        self.submitter_id = submitter_id
        self.translated_lyrics = translated_lyrics
        self.latin_lyrics = latin_lyrics
        self.native_lyrics = native_lyrics
        self.lyrics_notes = lyrics_notes
        self.video_link = video_link
        self.poster_link = poster_link
        self.sources = sources
        self.recap_start = format_seconds(recap_start) if recap_start is not None else None
        self.recap_end = format_seconds(recap_end) if recap_end is not None else None
        self.title_lang = get_language(title_lang) if title_lang else Language()
        self.native_lang = get_language(native_lang) if native_lang else Language()
        if show_id is not None and ro is not None:
            self.vote_data = get_votes_for_song(self.id, show_id, ro)
        elif ro is not None:
            self.vote_data = VoteData(ro, None, None, None)
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
            'country': self.country,
            'year': self.year,
            'placeholder': self.placeholder,
            'languages': [lang.as_dict() for lang in self.languages],
            'submitter': self.submitter,
            'native_title': self.native_title,
            'title_lang': self.title_lang.as_dict() if self.title_lang else None,
            'native_lang': self.native_lang.as_dict() if self.native_lang else None,
            'vote_data': self.vote_data.as_dict() if self.vote_data else None
        }

    def get_pt(self, points: int) -> int | None:
        if self.vote_data is None:
            return None
        return self.vote_data.get_pt(points)

@total_ordering
@dataclass
class Show:
    year: int | None
    short_name: str
    name: str
    date: datetime.date

    def __init__(self, *, year: int | None, short_name: str, name: str, date: datetime.date):
        self.year = year
        self.short_name = short_name
        self.name = name
        self.date = date

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

        if self.year is None or other.year is None:
            return self.date < other.date

        if self.year != other.year:
            return self.year < other.year

        v1 = value_map(self.short_name)
        v2 = value_map(other.short_name)
        return v1 < v2

def format_timedelta(td: datetime.timedelta | None) -> str | None:
    if td is None:
        return None
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

def get_show_id(show: str, year: int | None = None) -> ShowData | None:
    db = get_db()
    cursor = db.cursor()

    if year:
        short_show_name = show
    else:
        # Format: "year-show" e.g. "2025-f" or "cs24-f" for specials
        parts = show.split('-', 1)
        if len(parts) == 2:
            short_show_name = parts[1]
            try:
                year = int(parts[0])
            except ValueError:
                # Non-numeric prefix: look up as special short name
                cursor.execute('SELECT id FROM year WHERE special_short_name = %s', (parts[0],))
                row = cursor.fetchone()
                if not row:
                    return None
                year = row['id']
        else:
            return None

    cursor.execute('''
        SELECT id, point_system_id, show_name, voting_opens, voting_closes, predictions_close, dtf, sc, special, status FROM show
        WHERE year_id = %s AND short_name = %s
    ''', (year, short_show_name))

    show_row = cursor.fetchone()
    if show_row:
        show_id = show_row['id']
        point_system_id = show_row['point_system_id']
        show_name = show_row['show_name']
        voting_opens = show_row['voting_opens']
        voting_closes = show_row['voting_closes']
        predictions_close = show_row['predictions_close']
        dtf = show_row['dtf']
        sc = show_row['sc']
        special = show_row['special']
        status = show_row['status']
    else:
        return None

    points = get_points_for_system(point_system_id)

    ret = ShowData(
        id=show_id,
        points=list(points),
        point_system_id=point_system_id,
        name=show_name,
        short_name=short_show_name,
        voting_opens=voting_opens,
        voting_closes=voting_closes,
        predictions_close=predictions_close,
        year=year,
        dtf=dtf,
        sc=sc,
        special=special,
        status=status
    )

    return ret

def get_points_for_system(point_system_id: int) -> list[int]:
    db = get_db()
    cursor = db.cursor()

    points = []
    cursor.execute('''
        SELECT score FROM point
        WHERE point_system_id = %s
        ORDER BY place
    ''', (point_system_id,))
    for p in cursor.fetchall():
        points.append(p['score'])

    return points

def get_countries(only_participating: bool = False) -> list[Country]:
    if only_participating:
        query = "SELECT id, name, is_participating, cc3 FROM country WHERE is_participating AND id <> 'XX' ORDER BY name"
    else:
        query = "SELECT id, name, is_participating, cc3 FROM country WHERE id <> 'XX' ORDER BY name"
    db = get_db()
    cursor = db.cursor()

    cursor.execute(query)
    countries = [
        Country(
            cc=row['id'],
            name=row['name'],
            is_participating=bool(row['is_participating']),
            cc3=row['cc3']
        )
        for row in cursor.fetchall()
    ]
    return countries

def get_user_id_from_session(session_id: str | None) -> tuple[int, str] | None:
    if not session_id:
        return None
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT account.id, account.username FROM session
        JOIN account ON session.user_id = account.id
        WHERE session.session_id = %s AND session.expires_at > CURRENT_TIMESTAMP
    ''', (session_id,))
    row = cursor.fetchone()
    if row:
        return (row['id'], row['username'])
    return None

def get_user_role_from_session(session_id: str | None) -> UserPermissions:
    if not session_id:
        return UserPermissions()
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT account_role.name, account_role.can_edit, account_role.can_view_restricted
        FROM session
        JOIN account ON session.user_id = account.id
        JOIN account_role ON account.role = account_role.name
        WHERE session.session_id = %s AND session.expires_at > CURRENT_TIMESTAMP
    ''', (session_id,))
    row = cursor.fetchone()
    if row:
        return UserPermissions(role=row['name'],
                               can_edit=row['can_edit'],
                               can_view_restricted=row['can_view_restricted'])
    return UserPermissions()

def get_user_permissions(user_id: int | None) -> UserPermissions:
    if user_id is None:
        return UserPermissions()
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT account_role.name, account_role.can_edit, account_role.can_view_restricted
        FROM account
        JOIN account_role ON account.role = account_role.name
        WHERE account.id = %s
    ''', (user_id,))
    row = cursor.fetchone()
    if row:
        return UserPermissions(role=row['name'],
                               can_edit=row['can_edit'],
                               can_view_restricted=row['can_view_restricted'])
    return UserPermissions()

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

def get_votes_for_song(song_id: int, show_id: int, ro: int) -> VoteData:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT total_points, total_votes_received, point_distribution,
               max_pts, total_voters
        FROM country_show_results
        WHERE song_id = %s AND show_id = %s
    ''', (song_id, show_id))

    row = cursor.fetchone()
    if row:
        res = VoteData(ro=ro, total_votes=row['total_votes_received'],
                       max_pts=row['max_pts'], show_voters=row['total_voters'])
        res.sum = row['total_points']
        res.count = row['total_votes_received']
        for pt_str, cnt in (row['point_distribution'] or {}).items():
            res.pts[int(pt_str)] = cnt
        return res

    # Fallback: query raw vote table if cache is not yet populated
    cursor.execute('''
        SELECT COUNT(*) AS c FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        WHERE song_id = %s AND show_id = %s
    ''', (song_id, show_id))
    count = fetchone(cursor)['c']

    cursor.execute('''
        SELECT MAX(score) AS m FROM show
        JOIN point ON show.point_system_id = point.point_system_id
        WHERE show.id = %s
    ''', (show_id,))
    max_pts = fetchone(cursor)['m']

    cursor.execute('''
        SELECT COUNT(DISTINCT voter_id) AS c FROM vote_set
        WHERE show_id = %s
    ''', (show_id,))
    show_voters = fetchone(cursor)['c']

    cursor.execute('''
        SELECT score FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        WHERE song_id = %s AND show_id = %s
        ORDER BY score
    ''', (song_id, show_id))
    res = VoteData(ro=ro, total_votes=count, max_pts=max_pts, show_voters=show_voters)
    for points in cursor.fetchall():
        pt = points['score']
        res.sum += pt
        res.count += 1
        res.pts[pt] += 1
    return res

def format_seconds(seconds: int | None) -> str:
    """Format seconds into a string in the format M:SS."""
    if seconds is None or seconds <= 0:
        return ""
    minutes = seconds // 60
    seconds %= 60
    return f"{minutes}:{seconds:02}"

def parse_seconds(td: str | None) -> int | None:
    """Parse a string in the format M:SS into seconds."""
    if td is None:
        return None
    parts = list(map(int, td.split(':')))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    else:
        raise ValueError("Invalid time format. Use 'M:SS'.")

@lru_cache(maxsize=512)
def get_language(lang_id: int) -> Language | None:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT name, tag, extlang, region, subvariant, suppress_script FROM language
        WHERE id = %s
    ''', (lang_id,))
    lang = cursor.fetchone()
    if not lang:
        return None

    return Language(**lang)

def get_song_languages(song_id: int) -> list[Language]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT language.name, language.tag, language.extlang, language.region, language.subvariant, language.suppress_script FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_id = %s
        ORDER BY priority
    ''', (song_id,))
    languages = [Language(**lang) for lang in cursor.fetchall()]

    return languages

def get_languages_for_songs(song_ids: list[int]) -> dict[int, list[Language]]:
    """Batch-load languages for many songs in a single query."""
    if not song_ids:
        return {}

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song_language.song_id,
               language.name, language.tag, language.extlang,
               language.region, language.subvariant, language.suppress_script
        FROM song_language
        JOIN language ON song_language.language_id = language.id
        WHERE song_language.song_id = ANY(%s)
        ORDER BY song_language.song_id, song_language.priority
    ''', (song_ids,))

    result: dict[int, list[Language]] = {sid: [] for sid in song_ids}
    for row in cursor.fetchall():
        sid = row.pop('song_id')
        result[sid].append(Language(**row))
    return result

def get_show_songs(year: int | None, short_name: str, *, select_languages=False, select_votes=False, sort_reveal = False) -> list[Song] | None:
    db = get_db()
    cursor = db.cursor()
    data = get_show_id(short_name, year)

    if not data:
        return None
    show_id = data.id

    additional_sort = ''
    if sort_reveal:
        additional_sort = 'song_show.qualifier_order,'

    cursor.execute(f'''
        SELECT song.id, song.title, song.artist, song.native_title,
               song.country_id, country.name, country.is_participating, country.cc3,
               song.year_id, song_show.running_order, song.is_placeholder,
               song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
               account.username, song.title_language_id, song.native_language_id,
               song.video_link, song.snippet_start, song.snippet_end,
               song.submitter_id, song.notes, song.sources, song.poster_link,
               song.entry_number
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN show ON song_show.show_id = show.id
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        WHERE show.id = %s
        ORDER BY {additional_sort} song_show.running_order, song_show.id
        ''', (show_id,))
    rows = cursor.fetchall()
    songs = []
    for row in rows:
        s = Song(RawSongData(row, show_id=show_id if select_votes else None))
        s.entry_number = row['entry_number']
        songs.append(s)

    if select_languages:
        languages_by_song = get_languages_for_songs([s.id for s in songs])
        for song in songs:
            song.languages = languages_by_song.get(song.id, [])

    return songs

def get_show_winner(year: int | None, show: str) -> Song | None:
    songs = get_show_songs(year, show, select_votes=True)
    if not songs:
        return None

    songs.sort(reverse=True)
    winner = songs[0]
    winner.languages = get_song_languages(winner.id)

    return winner

def get_year_winner(year: int) -> Song | None:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT status FROM year
        WHERE id = %s
        ''', (year,))

    if fetchone(cursor)['status'] != 'closed':
        return None

    return get_show_winner(year, 'f')

def get_special_winner(show: str, year: int) -> Song | None:
    return get_show_winner(year, show)

def get_year_songs(year: int, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
               song.country_id, country.name, country.is_participating, country.cc3,
               song.is_placeholder, account.username, song.year_id, song.poster_link,
               song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
               song.title_language_id, song.native_language_id,
               song.video_link, song.snippet_start, song.snippet_end,
               song.submitter_id, song.notes, song.sources, song.entry_number
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        LEFT JOIN year ON year.id = song.year_id
        LEFT JOIN country_year_results cyr ON cyr.song_id = song.id
        WHERE song.year_id = %s
        ORDER BY
            CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
            country.name
        ''', (year,))
    songs = []
    for row in cursor.fetchall():
        s = Song(RawSongData(row))
        s.entry_number = row['entry_number']
        songs.append(s)

    if select_languages:
        languages_by_song = get_languages_for_songs([s.id for s in songs])
        for song in songs:
            song.languages = languages_by_song.get(song.id, [])

    return songs


def get_year_placements(year: int) -> dict[int, int]:
    """Return {song_id: place} from country_year_results for a closed year."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song_id, place FROM country_year_results WHERE year_id = %s
    ''', (year,))
    return {row['song_id']: row['place'] for row in cursor.fetchall()}

def get_user_songs(user_id: int, year: int | None = None, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    if year:
        cursor.execute('''
            SELECT song.id, song.title, song.artist, song.native_title,
                   song.country_id, country.name, country.is_participating, country.cc3,
                   song.is_placeholder, song.native_language_id, song.title_language_id,
                   song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                   account.username, song.year_id, song.poster_link,
                   song.video_link, song.snippet_start, song.snippet_end,
                   song.submitter_id, song.notes, song.sources,
                   song.entry_number, year.special_name, year.special_short_name
            FROM song
            JOIN country ON song.country_id = country.id
            LEFT OUTER JOIN account ON song.submitter_id = account.id
            LEFT JOIN year ON year.id = song.year_id
            LEFT JOIN country_year_results cyr ON cyr.song_id = song.id
            WHERE song.submitter_id = %s AND song.year_id = %s AND song.year_id IS NOT NULL
            ORDER BY song.year_id,
                     CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
                     country.name
        ''', (user_id, year))
    else:
        cursor.execute('''
            SELECT song.id, song.title, song.artist, song.native_title,
                   song.country_id, country.name, country.is_participating, country.cc3,
                   song.is_placeholder, song.native_language_id, song.title_language_id,
                   song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                   account.username, song.year_id, song.poster_link,
                   song.video_link, song.snippet_start, song.snippet_end,
                   song.submitter_id, song.notes, song.sources,
                   song.entry_number, year.special_name, year.special_short_name
            FROM song
            JOIN country ON song.country_id = country.id
            LEFT OUTER JOIN account ON song.submitter_id = account.id
            LEFT JOIN year ON year.id = song.year_id
            LEFT JOIN country_year_results cyr ON cyr.song_id = song.id
            WHERE song.submitter_id = %s AND song.year_id IS NOT NULL
            ORDER BY song.year_id,
                     CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
                     country.name
        ''', (user_id,))
    songs = []
    for row in cursor.fetchall():
        s = Song(RawSongData(row))
        s.entry_number = row['entry_number']
        s.special_name = row['special_name']
        s.special_short_name = row['special_short_name']
        songs.append(s)

    if select_languages:
        languages_by_song = get_languages_for_songs([s.id for s in songs])
        for song in songs:
            song.languages = languages_by_song.get(song.id, [])
    return songs

def get_show_results_for_songs(song_ids: list[int]) -> dict[int, dict]:
    """Return published show results for a list of song IDs.

    Returns a dict keyed by song_id.  Each value is a dict with keys
    'f', 'sc', 'sf' (or absent when the entry didn't participate in
    that round).  Each present value is a dict with 'pts', 'place',
    and 'show_name'.  Only rows from fully-published shows
    (status = 'full') are included.
    """
    if not song_ids:
        return {}

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT csr.song_id,
               csr.short_name,
               csr.total_points AS pts,
               csr.place,
               csr.total_countries,
               csr.show_name
        FROM country_show_results csr
        JOIN show ON show.id = csr.show_id
        WHERE csr.song_id = ANY(%s)
          AND show.status = 'full'
        ORDER BY csr.song_id, csr.year_id, csr.short_name
    ''', (song_ids,))

    results: dict[int, dict] = {}
    for row in cursor.fetchall():
        sid = row['song_id']
        if sid not in results:
            results[sid] = {}
        sn = row['short_name']
        if sn == 'f':
            key = 'f'
        elif sn == 'sc':
            key = 'sc'
        elif sn and (sn == 'sf' or sn.startswith('sf')):
            key = 'sf'
        else:
            continue
        # Keep the first match per key (there should be at most one per type)
        if key not in results[sid]:
            results[sid][key] = {
                'pts': row['pts'],
                'place': row['place'],
                'total_countries': row['total_countries'],
                'show_name': row['show_name'],
                'short_name': row['short_name'],
            }

    # Year-level placements (only for closed years)
    cursor.execute('''
        SELECT cyr.song_id, cyr.place, cyr.total_countries
        FROM country_year_results cyr
        JOIN year ON year.id = cyr.year_id
        WHERE cyr.song_id = ANY(%s)
          AND year.status = 'closed'
    ''', (song_ids,))
    for row in cursor.fetchall():
        sid = row['song_id']
        if sid not in results:
            results[sid] = {}
        results[sid]['year'] = {
            'place': row['place'],
            'total_countries': row['total_countries'],
        }

    return results


def get_country_songs(code: str, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.is_participating, country.cc3,
                song.is_placeholder, song.native_language_id, song.title_language_id,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                account.username, song.year_id, song.poster_link,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources,
                song.entry_number, year.special_name, year.special_short_name
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        LEFT JOIN year ON year.id = song.year_id
        LEFT JOIN country_year_results cyr ON cyr.song_id = song.id
        WHERE (song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id IS NOT NULL
        ORDER BY song.year_id,
                 CASE WHEN year.status = 'closed' THEN cyr.place END NULLS LAST,
                 country.name
    ''', {'cc':code})
    songs = []
    for row in cursor.fetchall():
        s = Song(RawSongData(row))
        s.entry_number = row['entry_number']
        s.special_name = row['special_name']
        s.special_short_name = row['special_short_name']
        songs.append(s)

    if select_languages:
        languages_by_song = get_languages_for_songs([s.id for s in songs])
        for song in songs:
            song.languages = languages_by_song.get(song.id, [])
    return songs

def get_song(year: int, code: str, *, select_results=False) -> Song | None:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.is_participating, country.cc3,
                song.is_placeholder, song.native_language_id, song.title_language_id,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                account.username, song.year_id, song.poster_link,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        WHERE (song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id = %(year)s
        ORDER BY song.year_id, country.name
    ''', {'cc': code, 'year': year})
    song = cursor.fetchone()
    if not song:
        return None
    ret = Song(RawSongData(song))

    ret.languages = get_song_languages(ret.id)
    return ret

def get_special_songs_for_country(year: int, code: str) -> list[Song]:
    """Get all songs for a country in a special (negative year_id)."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.is_participating, country.cc3,
                song.is_placeholder, song.native_language_id, song.title_language_id,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                account.username, song.year_id, song.poster_link,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources, song.entry_number
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        WHERE (song.country_id = %(cc)s OR country.cc3 = %(cc)s) AND song.year_id = %(year)s
        ORDER BY song.entry_number
    ''', {'cc': code, 'year': year})

    songs = []
    for row in cursor.fetchall():
        s = Song(RawSongData(row))
        s.languages = get_song_languages(s.id)
        s.entry_number = row['entry_number']
        songs.append(s)
    return songs

def get_special_song(year: int, code: str, entry_number: int) -> Song | None:
    """Get a specific song by country and entry_number in a special."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.is_participating, country.cc3,
                song.is_placeholder, song.native_language_id, song.title_language_id,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                account.username, song.year_id, song.poster_link,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources, song.entry_number
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        WHERE (song.country_id = %(cc)s OR country.cc3 = %(cc)s)
          AND song.year_id = %(year)s AND song.entry_number = %(entry)s
    ''', {'cc': code, 'year': year, 'entry': entry_number})
    row = cursor.fetchone()
    if not row:
        return None
    s = Song(RawSongData(row))
    s.languages = get_song_languages(s.id)
    s.entry_number = row['entry_number']
    return s

def get_years() -> list[int]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id FROM year
    ''')
    return list(map(lambda x: x['id'], cursor.fetchall()))

def get_years_grouped() -> dict:
    """Return years split into groups for display in submission forms:
      - open: status = 'open', ascending
      - closed: status <> 'open', ascending
      - specials: negative IDs with their special_name / special_short_name
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, status, special_name, special_short_name
        FROM year
        ORDER BY id
    ''')
    open_years: list[int] = []
    closed_years: list[int] = []
    specials: list[dict] = []
    for row in cursor.fetchall():
        if row['id'] < 0:
            specials.append({
                'id': row['id'],
                'special_name': row['special_name'],
                'special_short_name': row['special_short_name'],
            })
        elif row['status'] == 'open':
            open_years.append(row['id'])
        else:
            closed_years.append(row['id'])
    return {'open': open_years, 'closed': closed_years, 'specials': specials}

def get_year_countries(year: int, *, exclude: list[str] = [], sort_by_priority: bool = False, host: bool = True) -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    if sort_by_priority:
        order_by = "priority"
    else:
        order_by = "country.name"

    add = ""
    if not host:
        add = "AND country.id <> year.host_id"

    cursor.execute(f'''
        SELECT country.id AS cc, country.name, country.pot, song.submitter_id AS submitter FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON song.year_id = year.id {add}
        WHERE song.year_id = %s
        ORDER BY {order_by}
    ''', (year,))
    countries = cursor.fetchall()

    return countries

def get_year_shows(year: int, pattern: str = '') -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT show_name, short_name FROM show
        WHERE year_id = %s AND short_name LIKE %s COLLATE "C"
    ''', (year, pattern + '%'))

    shows = cursor.fetchall()

    return shows

def get_vote_count_for_show(show_id: int) -> int:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT COUNT(*) AS c FROM vote_set
        WHERE show_id = %s
    ''', (show_id,))
    count = fetchone(cursor)['c']
    return count

def resolve_country_code(code: str) -> str | None:
    """Resolve a country code (cc2 or cc3) to the canonical cc2 id. Returns None if not found."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id FROM country
        WHERE id = %(cc)s OR cc3 = %(cc)s
    ''', {'cc': code})
    row = cursor.fetchone()
    return row['id'] if row else None

def get_country_name(country_id: str) -> str:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT name FROM country
        WHERE id = %(cc)s OR cc3 = %(cc)s
    ''', {'cc':country_id})
    country_name = cursor.fetchone()
    if country_name:
        return country_name['name']
    return "Unknown"

def render_template(template: str, **kwargs) -> Response:
    resp = Response()
    if request.accept_mimetypes.accept_html:
        resp.data = flask.render_template(template, **kwargs)
        resp.content_type = 'text/html'
    elif request.accept_mimetypes.accept_json:
        resp.data = json.dumps(kwargs)
        resp.content_type = 'application/json'
    else:
        resp.data = f"Invalid format. Accepted MIME types are: [{request.accept_mimetypes}] for UA '{request.headers.get("User-Agent", '')}'"
        resp.content_type = 'text/plain'

    return resp

def footnote_plugin(md: MarkdownIt):
    def tokenize_footnote(state: StateInline, silent: bool):
        src = state.src[state.pos:]
        match = re.match(r'\[(\d+)\]', src)
        if not match:
            return False

        if silent:
            return False

        number = match.group(1)
        state.pos += match.end()

        token = state.push('footnote_open', 'a', 1)
        token.attrs = {
            'href': f'#footnote-{number}',
            'class': 'footnote-link'
        }

        token = state.push('sup_open', 'sup', 1)
        token = state.push('text', '', 0)
        token.content = f'[{number}]'
        token = state.push('sup_close', 'sup', -1)

        token = state.push('footnote_close', 'a', -1)

        return True

    md.inline.ruler.before('emphasis', 'footnote', tokenize_footnote)

    def render_token(self, tokens, idx, options, env):
        token = tokens[idx]
        if token.type == 'footnote_open':
            href = token.attrs['href']
            cls = token.attrs['class']
            return f'<a href="{href}" class="{cls}">'
        elif token.type == 'footnote_close':
            return '</a>'
        return ''

    md.add_render_rule('footnote_open', render_token)
    md.add_render_rule('footnote_close', render_token)

def make_bbcode_plugin(allowed_colours):
    tags = {
        'b': 'strong',
        'i': 'em',
        'u': 'ins',
        's': 'del',
        'sm': 'small',
        'xl': 'big'
    }

    c_re = re.compile(r'\[c=([a-zA-Z]+)\]')
    close_re = {
        'b': '[/b]',
        'i': '[/i]',
        'u': '[/u]',
        's': '[/s]',
        'sm': '[/sm]',
        'xl': '[/xl]',
        'c': '[/c]',
    }

    def bbcode_plugin(md: MarkdownIt):

        def tokenizer(state: StateInline, silent: bool):
            src = state.src
            pos = state.pos

            for tag, html_tag in tags.items():
                open_tag = f'[{tag}]'
                close_tag = close_re[tag]
                if src.startswith(open_tag, pos):
                    end_pos = src.find(close_tag, pos + len(open_tag))
                    if end_pos == -1:
                        return False
                    if silent:
                        return True

                    old_pos, old_max = state.pos, state.posMax
                    state.pos = pos + len(open_tag)
                    state.posMax = end_pos

                    state.push(f'bb_{tag}_open', html_tag, 1)
                    state.md.inline.tokenize(state)
                    state.push(f'bb_{tag}_close', html_tag, -1)

                    state.pos = end_pos + len(close_tag)
                    state.posMax = old_max
                    return True

            m = c_re.match(src, pos)
            if m:
                colour = m.group(1)

                open_len = m.end()
                close_tag = close_re['c']
                end_pos = src.find(close_tag, open_len)
                if end_pos == -1:
                    return False
                if silent:
                    return True

                old_pos, old_max = state.pos, state.posMax
                state.pos = open_len
                state.posMax = end_pos

                token = state.push('bb_colour_open', 'span', 1)
                if colour in allowed_colours:
                    token.attrs = {'class': f'colour-{colour}'}
                state.md.inline.tokenize(state)
                state.push('bb_colour_close', 'span', -1)

                state.pos = end_pos + len(close_tag)
                state.posMax = old_max
                return True

            return False

        md.inline.ruler.before('emphasis', 'bbcode_all', tokenizer)

        def simple_open(tag):
            def render(self, tokens, idx, opts, env):
                return f'<{tag}>'
            return render

        def simple_close(tag):
            def render(self, tokens, idx, opts, env):
                return f'</{tag}>'
            return render

        for tag, html_tag in tags.items():
            md.add_render_rule(f'bb_{tag}_open', simple_open(html_tag))
            md.add_render_rule(f'bb_{tag}_close', simple_close(html_tag))

        # Register colour
        def render_colour_open(self, tokens, idx, opts, env):
            klass = tokens[idx].attrs['class']
            return f'<span class="{klass}">'

        md.add_render_rule('bb_colour_open', render_colour_open)
        md.add_render_rule('bb_colour_close', simple_close('span'))

    return bbcode_plugin

def make_entity_plugin(entities=None):
    if entities is None:
        entities = {
            "nbsp": "\u00A0",
            "shy": "\u00AD",
            "tab": "\t",
            "amp": "&",
            "ensp": " ",
            "emsp": " ",
            "ndash": "–",
            "mdash": "—",
            "ellip": "…"
        }

    def entity_plugin(md: MarkdownIt):
        def tokenizer(state: StateInline, silent: bool):
            src = state.src
            pos = state.pos

            if not src.startswith("&", pos):
                return False

            semi = src.find(";", pos + 1)
            if semi == -1:
                return False

            name = src[pos + 1:semi]
            if name not in entities:
                return False

            if silent:
                return True

            token = state.push("entity", "", 0)
            token.content = entities[name]

            state.pos = semi + 1
            return True

        md.inline.ruler.before("text", "entities", tokenizer)

        def render_entity(self, tokens, idx, opts, env):
            return tokens[idx].content

        md.add_render_rule("entity", render_entity)

    return entity_plugin

@lru_cache(maxsize=1)
def get_markdown_parser():
    colours = {'red', 'green', 'blue', 'yellow', 'magenta', 'cyan'}
    md = (
        MarkdownIt('zero')
        .enable(['emphasis'])
        .use(footnote_plugin)
        .use(make_bbcode_plugin(colours))
        .use(make_entity_plugin())
        )
    return md

def resp(data: Any, code: int = 200) -> tuple[dict[str, Any], int]:
    return {
        "result": data
    }, code

class ErrorID(Enum):
    NONE = 0
    NOT_FOUND = 1
    UNAUTHORIZED = 2
    FORBIDDEN = 3
    BAD_REQUEST = 4
    CONFLICT = 5

    def http_code(self):
        match self:
            case ErrorID.NONE:
                return 200
            case ErrorID.NOT_FOUND:
                return 404
            case ErrorID.UNAUTHORIZED:
                return 401
            case ErrorID.FORBIDDEN:
                return 403
            case ErrorID.BAD_REQUEST:
                return 400
            case ErrorID.CONFLICT:
                return 409
            case _:
                return 400

def err(id: ErrorID, desc: str) -> tuple[dict[str, Any], int]:
    return ({
        "error": {
            "id": id.value,
            "description": desc
        }
    }, id.http_code())

def url_bool(datum: str) -> bool:
    return datum in ('true', '1', 'y', 'on', 'yes', 'y')


# ── API token helpers ──────────────────────────────────────────────

import hashlib
import secrets

def generate_api_token() -> tuple[str, bytes]:
    """Generate a new API token. Returns (plaintext_token, token_hash)."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).digest()
    return token, token_hash

def hash_api_token(token: str) -> bytes:
    """Hash a plaintext API token for database lookup."""
    return hashlib.sha256(token.encode()).digest()

def get_user_from_api_token(token: str) -> tuple[int, str] | None:
    """Look up a user by API token. Returns (user_id, username) or None."""
    token_hash = hash_api_token(token)
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT account.id, account.username FROM api_token
        JOIN account ON api_token.user_id = account.id
        WHERE api_token.token_hash = %s AND account.approved
    ''', (token_hash,))
    row = cursor.fetchone()
    if row:
        cursor.execute('''
            UPDATE api_token SET last_used_at = CURRENT_TIMESTAMP
            WHERE token_hash = %s
        ''', (token_hash,))
        db.commit()
        return (row['id'], row['username'])
    return None

def get_api_auth() -> tuple[int, str, UserPermissions] | None:
    """Authenticate an API request via Bearer token or session cookie.
    Returns (user_id, username, permissions) or None."""
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token = auth[7:]
        result = get_user_from_api_token(token)
        if result:
            user_id, username = result
            perms = get_user_permissions(user_id)
            return (user_id, username, perms)
        return None

    # Fall back to session cookie
    session_id = request.cookies.get('session')
    if session_id:
        result = get_user_id_from_session(session_id)
        if result:
            user_id, username = result
            perms = get_user_permissions(user_id)
            return (user_id, username, perms)
    return None