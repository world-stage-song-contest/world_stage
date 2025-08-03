from collections import defaultdict, deque
import datetime
from enum import Enum
from functools import total_ordering
import json
from typing import Optional, Union

from flask import Response, request
import flask
from .db import get_db
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Deque, TypeAlias
import urllib.parse

from functools import lru_cache
from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
import re

Bucket: TypeAlias = Deque[Tuple[str, Dict[int, int]]]

class LCG:
    def __init__(self, seed: int, a: int = 0x19660d, c: int = 0x3c6ef35f, m: int = 2**32) -> None:
        self.state = seed
        self.a = a
        self.c = c
        self.m = m
        self.seed = seed

    def next(self) -> int:
        self.state = (self.state * self.a + self.c) % self.m
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
            num = lcg.next() % self.first_half
            final_order.insert(num, v)

        return final_order

@dataclass
class ShowData:
    id: int
    points: list[int]
    point_system_id: int
    name: str
    short_name: str
    voting_opens: datetime.datetime | None
    voting_closes: datetime.datetime | None
    year: int | None
    dtf: int | None
    sc: int | None
    special: int | None
    access_type: str

class UserPermissions(Enum):
    NONE = 0
    USER = 1
    EDITOR = 2
    ADMIN = 3
    OWNER = 4

    @staticmethod
    def from_str(role: str) -> 'UserPermissions':
        if role == 'user':
            return UserPermissions.USER
        elif role == 'editor':
            return UserPermissions.EDITOR
        elif role == 'admin':
            return UserPermissions.ADMIN
        elif role == 'owner':
            return UserPermissions.OWNER
        else:
            return UserPermissions.NONE

    def __str__(self) -> str:
        if self == UserPermissions.USER:
            return 'user'
        elif self == UserPermissions.EDITOR:
            return 'editor'
        elif self == UserPermissions.ADMIN:
            return 'admin'
        elif self == UserPermissions.OWNER:
            return 'owner'
        else:
            return 'none'

    @property
    def can_view_restricted(self):
        return self == UserPermissions.ADMIN or self == UserPermissions.OWNER

    @property
    def can_edit(self):
        return self == UserPermissions.EDITOR or self == UserPermissions.ADMIN or self == UserPermissions.OWNER

@dataclass
class Country:
    cc: str
    name: str
    is_participating: bool
    bg: str
    fg1: str
    fg2: str
    text: str

    def __init__(self, *, cc: str, name: str, is_participating: bool, bg: str, fg1: str, fg2: str, text: str):
        self.cc = cc
        self.name = name
        self.is_participating = is_participating
        self.bg = bg
        self.fg1 = fg1
        self.fg2 = fg2
        self.text = text

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
        for i in range(overall_max):
            this_v = self.pts.get(i, 0)
            other_v = other.pts.get(i, 0)
            if this_v != other_v:
                return this_v < other_v
        if self.ro is None or other.ro is None:
            return False
        return self.ro > other.ro

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

@dataclass
class Language:
    name: str
    tag: str
    extlang: Optional[str]
    region: Optional[str]
    subvariant: Optional[str]
    suppress_script: Optional[bool]

    def __init__(self, name: str = '', tag: str = '',
                 extlang: Optional[str] = None, region: Optional[str] = None,
                 subvariant: Optional[str] = None, suppress_script: Optional[bool] = None):
        self.name = name
        self.tag = tag
        self.extlang = extlang
        self.region = region
        self.subvariant = subvariant
        self.suppress_script = suppress_script

    def str(self, script: Optional[str] = None) -> str:
        components = [self.tag]
        if self.extlang:
            components.append(self.extlang)
        if script and script != self.suppress_script:
            components.append(script)
        if self.region:
            components.append(self.region)
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

@total_ordering
@dataclass
class Song:
    id: int
    title: str
    artist: str
    country: Country
    year: Optional[int]
    placeholder: bool
    languages: list[Language]
    vote_data: Optional[VoteData]
    submitter: Optional[str]
    submitter_id: Optional[int]
    native_title: Optional[str]
    title_lang: Optional[Language]
    native_lang: Optional[Language]
    english_lyrics: Optional[str]
    latin_lyrics: Optional[str]
    native_lyrics: Optional[str]
    lyrics_notes: Optional[str]
    video_link: Optional[str]
    recap_start: Optional[str]
    recap_end: Optional[str]
    sources: Optional[str]
    hidden: bool = False

    def __init__(self, *,
                 id: int, title: str, native_title: Optional[str], artist: str,
                 country: Country, year: Optional[int],
                 placeholder: bool, submitter: Optional[str], submitter_id: Optional[int],
                 title_lang: Optional[int], native_lang: Optional[int], lyrics_notes: Optional[str],
                 english_lyrics: Optional[str], latin_lyrics: Optional[str], native_lyrics: Optional[str],
                 video_link: Optional[str], recap_start: Optional[int], recap_end: Optional[int],
                 sources: Optional[str],
                 languages: list['Language'] = [], show_id: Optional[int] = None, ro: Optional[int] = None):
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
        self.english_lyrics = english_lyrics
        self.latin_lyrics = latin_lyrics
        self.native_lyrics = native_lyrics
        self.lyrics_notes = lyrics_notes
        self.video_link = video_link
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

    def get_pt(self, points: int) -> Optional[int]:
        if self.vote_data is None:
            return None
        return self.vote_data.get_pt(points)

@total_ordering
@dataclass
class Show:
    year: Optional[int]
    short_name: str
    name: str
    date: datetime.date

    def __init__(self, *, year: Optional[int], short_name: str, name: str, date: datetime.date):
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
            SELECT id, point_system_id, show_name, voting_opens, voting_closes, dtf, sc, special, allow_access_type FROM show
            WHERE year_id = ? AND short_name = ?
        ''', (year, short_show_name))
    else:
        cursor.execute('''
            SELECT id, point_system_id, show_name, voting_opens, voting_closes, dtf, sc, special, allow_access_type FROM show
            WHERE short_name = ? AND year_id IS NULL
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
        short_name=short_show_name,
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

def get_current_year() -> int:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM year
        WHERE closed > 0
        ORDER BY id DESC
        LIMIT 1
    ''')
    year = cursor.fetchone()[0]
    return year

def get_countries(only_participating: bool = False) -> list[Country]:
    if only_participating:
        query = "SELECT id, name, is_participating, bgr_colour, fg1_colour, fg2_colour, txt_colour FROM country WHERE is_participating = 1 AND id <> 'XXX' ORDER BY name"
    else:
        query = "SELECT id, name, is_participating, bgr_colour, fg1_colour, fg2_colour, txt_colour FROM country WHERE id <> 'XXX' ORDER BY name"
    db = get_db()
    cursor = db.cursor()

    cursor.execute(query)
    countries = [
        Country(
            cc=id,
            name=name,
            is_participating=bool(is_participating),
            bg=bgr_colour,
            fg1=fg1_colour,
            fg2=fg2_colour,
            text=txt_colour
        )
        for id, name, is_participating, bgr_colour, fg1_colour, fg2_colour, txt_colour in cursor.fetchall()
    ]
    return countries

def get_user_id_from_session(session_id: str | None) -> tuple[int, str] | None:
    if not session_id:
        return None
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT user.id, user.username FROM session
        JOIN user ON session.user_id = user.id
        WHERE session.session_id = ? AND session.expires_at > datetime('now')
    ''', (session_id,))
    row = cursor.fetchone()
    if row:
        return (row[0], row[1])
    return None

def get_user_role_from_session(session_id: str | None) -> UserPermissions:
    if not session_id:
        return UserPermissions.NONE
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
        return UserPermissions.from_str(role)
    return UserPermissions.NONE

def get_user_permissions(user_id: int | None) -> UserPermissions:
    if user_id is None:
        return UserPermissions.NONE
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT role FROM user WHERE id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    if row:
        role = row[0]
        return UserPermissions.from_str(role)
    return UserPermissions.NONE

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
    if td is None:
        return None
    parts = list(map(int, td.split(':')))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    else:
        raise ValueError("Invalid time format. Use 'MM:SS'.")

def get_language(lang_id: int) -> Optional[Language]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT name, tag, extlang, region, subvariant, suppress_script FROM language
        WHERE id = ?
    ''', (lang_id,))
    lang = cursor.fetchone()
    if not lang:
        return None

    return Language(lang[0], lang[1], lang[2], lang[3], lang[4], lang[5])

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

def get_show_songs(year: Optional[int], short_name: str, *, select_languages=False, select_votes=False, sort_reveal = False) -> Optional[list[Song]]:
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
               song.country_id, country.name, country.is_participating,
               country.bgr_colour, country.fg1_colour, country.fg2_colour, country.txt_colour,
               song.year_id, song_show.running_order, song.is_placeholder,
               song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
               user.username, song.title_language_id, song.native_language_id,
               song.video_link, song.snippet_start, song.snippet_end,
               song.submitter_id, song.notes, song.sources
        FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN show ON song_show.show_id = show.id
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN user on song.submitter_id = user.id
        WHERE show.id = ?
        ORDER BY {additional_sort} song_show.running_order, song_show.id
        ''', (show_id,))
    songs = [Song(id=song['id'],
                  title=song['title'],
                  native_title=song['native_title'],
                  artist=song['artist'],
                  video_link=song['video_link'],
                  recap_start=song['snippet_start'],
                  recap_end=song['snippet_end'],
                  country=Country(cc=song['country_id'],
                                    name=song['name'],
                                    is_participating=bool(song['is_participating']),
                                    bg=song['bgr_colour'],
                                    fg1=song['fg1_colour'],
                                    fg2=song['fg2_colour'],
                                    text=song['txt_colour']),
                  year=song['year_id'],
                  placeholder=bool(song['is_placeholder']),
                  submitter=song['username'],
                  submitter_id=song['submitter_id'],
                  title_lang=song['title_language_id'],
                  native_lang=song['native_language_id'],
                  english_lyrics=song['translated_lyrics'],
                  latin_lyrics=song['romanized_lyrics'],
                  native_lyrics=song['native_lyrics'],
                  lyrics_notes=song['notes'],
                  ro=song['running_order'],
                  sources=song['sources'],
                  show_id=show_id if select_votes else None)
                for song in cursor.fetchall()]

    if select_languages:
        for song in songs:
            song.languages = get_song_languages(song.id)

    return songs

def get_show_winner(year: Optional[int], show: str) -> Optional[Song]:
    songs = get_show_songs(year, show, select_votes=True)
    if not songs:
        return None

    songs.sort(reverse=True)
    winner = songs[0]
    winner.languages = get_song_languages(winner.id)

    return winner

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

    return get_show_winner(year, 'f')

def get_special_winner(show: str) -> Optional[Song]:
    return get_show_winner(None, show)

def get_year_songs(year: int, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(f'''
        SELECT song.id, song.title, song.artist, song.native_title,
               song.country_id, country.name, country.is_participating,
               country.bgr_colour, country.fg1_colour, country.fg2_colour, country.txt_colour,
               song.is_placeholder, user.username, song.year_id,
               song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
               song.title_language_id, song.native_language_id,
               song.video_link, song.snippet_start, song.snippet_end,
               song.submitter_id, song.notes, song.sources
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN user on song.submitter_id = user.id
        WHERE song.year_id = ?
        ORDER BY country.name
        ''', (year,))
    songs = [Song(id=song['id'],
                  title=song['title'],
                  native_title=song['native_title'],
                  artist=song['artist'],
                  video_link=song['video_link'],
                  recap_start=song['snippet_start'],
                  recap_end=song['snippet_end'],
                  country=Country(cc=song['country_id'],
                                    name=song['name'],
                                    is_participating=bool(song['is_participating']),
                                    bg=song['bgr_colour'],
                                    fg1=song['fg1_colour'],
                                    fg2=song['fg2_colour'],
                                    text=song['txt_colour']),
                  placeholder=bool(song['is_placeholder']),
                  year=song['year_id'],
                  submitter_id=song['submitter_id'],
                  title_lang=song['title_language_id'],
                  native_lang=song['native_language_id'],
                  english_lyrics=song['translated_lyrics'],
                  latin_lyrics=song['romanized_lyrics'],
                  native_lyrics=song['native_lyrics'],
                  lyrics_notes=song['notes'],
                  sources=song['sources'],
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
            SELECT song.id, song.title, song.artist, song.native_title,
                   song.country_id, country.name, country.is_participating,
                   country.bgr_colour, country.fg1_colour, country.fg2_colour, country.txt_colour,
                   song.is_placeholder, song.native_language_id, song.title_language_id,
                   song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                   user.username, song.year_id,
                   song.video_link, song.snippet_start, song.snippet_end,
                   song.submitter_id, song.notes, song.sources
            FROM song
            JOIN country ON song.country_id = country.id
            LEFT OUTER JOIN user on song.submitter_id = user.id
            WHERE song.submitter_id = ? AND song.year_id = ? AND song.year_id IS NOT NULL
            ORDER BY song.year_id, country.name
        ''', (user_id, year))
    else:
        cursor.execute('''
            SELECT song.id, song.title, song.artist, song.native_title,
                   song.country_id, country.name, country.is_participating,
                   country.bgr_colour, country.fg1_colour, country.fg2_colour, country.txt_colour,
                   song.is_placeholder, song.native_language_id, song.title_language_id,
                   song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                   user.username, song.year_id,
                   song.video_link, song.snippet_start, song.snippet_end,
                   song.submitter_id, song.notes, song.sources
            FROM song
            JOIN country ON song.country_id = country.id
            LEFT OUTER JOIN user on song.submitter_id = user.id
            WHERE song.submitter_id = ? AND song.year_id IS NOT NULL
            ORDER BY song.year_id, country.name
        ''', (user_id,))
    songs = [Song(id=song['id'],
                  title=song['title'],
                  native_title=song['native_title'],
                  artist=song['artist'],
                  video_link=song['video_link'],
                  recap_start=song['snippet_start'],
                  recap_end=song['snippet_end'],
                  country=Country(cc=song['country_id'],
                                    name=song['name'],
                                    is_participating=bool(song['is_participating']),
                                    bg=song['bgr_colour'],
                                    fg1=song['fg1_colour'],
                                    fg2=song['fg2_colour'],
                                    text=song['txt_colour']),
                  placeholder=bool(song['is_placeholder']),
                  year=song['year_id'],
                  submitter_id=song['submitter_id'],
                  title_lang=song['title_language_id'],
                  native_lang=song['native_language_id'],
                  english_lyrics=song['translated_lyrics'],
                  latin_lyrics=song['romanized_lyrics'],
                  native_lyrics=song['native_lyrics'],
                  lyrics_notes=song['notes'],
                  sources=song['sources'],
                  submitter=song['username']) for song in cursor.fetchall()]

    if select_languages:
        for song in songs:
            song.languages = get_song_languages(song.id)
    return songs

def get_country_songs(code: str, *, select_languages = False) -> list[Song]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.is_participating,
                country.bgr_colour, country.fg1_colour, country.fg2_colour, country.txt_colour,
                song.is_placeholder, song.native_language_id, song.title_language_id,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                user.username, song.year_id,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN user on song.submitter_id = user.id
        WHERE (song.country_id = ?1 OR country.cc2 = ?1) AND song.year_id IS NOT NULL
        ORDER BY song.year_id, country.name
    ''', (code,))
    songs = [Song(id=song['id'],
                  title=song['title'],
                  native_title=song['native_title'],
                  artist=song['artist'],
                  video_link=song['video_link'],
                  recap_start=song['snippet_start'],
                  recap_end=song['snippet_end'],
                  country=Country(cc=song['country_id'],
                                    name=song['name'],
                                    is_participating=bool(song['is_participating']),
                                    bg=song['bgr_colour'],
                                    fg1=song['fg1_colour'],
                                    fg2=song['fg2_colour'],
                                    text=song['txt_colour']),
                  placeholder=bool(song['is_placeholder']),
                  year=song['year_id'],
                  title_lang=song['title_language_id'],
                  submitter_id=song['submitter_id'],
                  native_lang=song['native_language_id'],
                  english_lyrics=song['translated_lyrics'],
                  latin_lyrics=song['romanized_lyrics'],
                  native_lyrics=song['native_lyrics'],
                  lyrics_notes=song['notes'],
                  sources=song['sources'],
                  submitter=song['username']) for song in cursor.fetchall()]

    if select_languages:
        for song in songs:
            song.languages = get_song_languages(song.id)
    return songs

def get_song(year: int, code: str, *, select_results=False) -> Song | None:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.is_participating,
                country.bgr_colour, country.fg1_colour, country.fg2_colour, country.txt_colour,
                song.is_placeholder, song.native_language_id, song.title_language_id,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                user.username, song.year_id,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN user on song.submitter_id = user.id
        WHERE (song.country_id = ?1 OR country.cc2 = ?1) AND song.year_id = ?2
        ORDER BY song.year_id, country.name
    ''', (code,year))
    song = cursor.fetchone()
    if not song:
        return None
    ret = Song(id=song['id'],
                  title=song['title'],
                  native_title=song['native_title'],
                  artist=song['artist'],
                  video_link=song['video_link'],
                  recap_start=song['snippet_start'],
                  recap_end=song['snippet_end'],
                  country=Country(cc=song['country_id'],
                                    name=song['name'],
                                    is_participating=bool(song['is_participating']),
                                    bg=song['bgr_colour'],
                                    fg1=song['fg1_colour'],
                                    fg2=song['fg2_colour'],
                                    text=song['txt_colour']),
                  placeholder=bool(song['is_placeholder']),
                  year=song['year_id'],
                  submitter_id=song['submitter_id'],
                  title_lang=song['title_language_id'],
                  native_lang=song['native_language_id'],
                  english_lyrics=song['translated_lyrics'],
                  latin_lyrics=song['romanized_lyrics'],
                  native_lyrics=song['native_lyrics'],
                  lyrics_notes=song['notes'],
                  sources=song['sources'],
                  submitter=song['username'])

    ret.languages = get_song_languages(ret.id)
    return ret

def get_years() -> list[int]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id FROM year
    ''')
    return list(map(lambda x: x[0], cursor.fetchall()))

def get_year_countries(year: int, exclude: list[str] = []) -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT country.id, country.name, country.pot FROM song
        JOIN country ON song.country_id = country.id
        WHERE song.year_id = ?
        ORDER BY country.name
    ''', (year,))
    countries = []
    for id, name, pot in cursor.fetchall():
        if id in exclude: continue
        countries.append({
            'cc': id,
            'name': name,
            'pot': pot
        })

    return countries

def get_year_shows(year: int, pattern: str = '') -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT show_name, short_name FROM show
        WHERE year_id = ? AND short_name LIKE ?
    ''', (year, pattern + '%'))

    shows = []
    for name, short in cursor.fetchall():
        shows.append({
            'name': name,
            'short_name': short
        })

    return shows

def get_vote_count_for_show(show_id: int) -> int:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT COUNT(*) FROM vote_set
        WHERE show_id = ?
    ''', (show_id,))
    count = cursor.fetchone()[0]
    return count

def get_country_name(country_id: str) -> str:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT name FROM country
        WHERE id = ?1 OR cc2 = ?1
    ''', (country_id,))
    country_name = cursor.fetchone()
    if country_name:
        return country_name[0]
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

@lru_cache(maxsize=1)
def get_markdown_parser():
    colours = {'red', 'green', 'blue', 'yellow', 'magenta', 'cyan'}
    md = (
        MarkdownIt('zero')
        .enable(['emphasis'])
        .use(footnote_plugin)
        .use(make_bbcode_plugin(colours))
        )
    return md