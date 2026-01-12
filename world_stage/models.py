from abc import ABC
from dataclasses import asdict, dataclass
from typing import Any, Self

@dataclass(kw_only=True)
class Model:
    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def by(cls, **kwargs) -> list[Self]:
        raise NotImplementedError

@dataclass(kw_only=True)
class Country(Model):
    id: str
    cc2: str
    name: str

@dataclass(kw_only=True)
class Year(Model):
    year: int
    status: str
    host: Country | None
    entry_count: int | None = None
    placeholder_count: int | None = None

@dataclass(kw_only=True)
class Language(Model):
    name: str
    tag: str
    extlang: str | None = None
    region: str | None = None
    subvariant: str | None = None
    suppress_script: str | None = None

@dataclass(kw_only=True)
class User(Model):
    id: int
    username: str
    approved: bool
    role: str

@dataclass(kw_only=True)
class Song(Model):
    id: int
    title: str
    artist: str
    country: Country
    year: int | None
    placeholder: bool
    languages: list[Language]
    submitter: User | None
    native_title: str | None
    title_language: Language | None
    native_language: Language | None
    translated_lyrics: str | None
    latin_lyrics: str | None
    native_lyrics: str | None
    lyrics_notes: str | None
    video_link: str | None
    poster_link: str | None
    recap_start: str | None
    recap_end: str | None
    sources: str | None
