from flask import Blueprint, Response, current_app, redirect, request, url_for

from world_stage.models import Country, Language, Song, User

from world_stage.db import get_db
from world_stage.utils import ErrorID, err, format_seconds, resp, url_bool

bp = Blueprint('country', __name__, url_prefix='/country')

@bp.get('/')
def index():
    all = request.args.get('all', type=url_bool)

    if all:
        query = "SELECT id, name, cc2 FROM country WHERE id <> 'XXX' ORDER BY name"
    else:
        query = "SELECT id, name, cc2 FROM country WHERE is_participating AND id <> 'XXX' ORDER BY name"
    db = get_db()
    cursor = db.cursor()

    cursor.execute(query)

    data = [Country(**val).to_json() for val in cursor.fetchall()]
    return resp(data)

@bp.get('/<id>')
def country(id: str):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, name, cc2 FROM country WHERE id = %s ORDER BY name", (id,))

    res = cursor.fetchone()
    if not res:
        return err(ErrorID.NOT_FOUND, f"Country {id} not found")

    return resp(Country(**res).to_json())

@bp.get('/<id>/songs')
def songs(id: str):
    db = get_db()
    cursor = db.cursor()

    def get_languages(song_id: int) -> list[Language]:
        cursor.execute('''
            SELECT name, tag, extlang, region, subvariant, suppress_script
            FROM language
            JOIN song_language ON song_language.language_id = language.id
            WHERE song_language.song_id = %s
        ''', (song_id,))

        return [Language(**d) for d in cursor.fetchall()]

    cursor.execute('''
        SELECT song.id, song.title, song.artist, song.native_title,
                song.country_id, country.name, country.cc2, song.is_placeholder,
                tl.name AS t_name, tl.tag AS t_tag, tl.extlang AS t_extlang,
                tl.region AS t_region, tl.subvariant AS t_subvariant, tl.suppress_script AS t_suppress_script,
                nl.name AS n_name, nl.tag AS n_tag, nl.extlang AS n_extlang,
                nl.region AS n_region, nl.subvariant AS n_subvariant, nl.suppress_script AS n_suppress_script,
                song.native_lyrics, song.romanized_lyrics, song.translated_lyrics,
                account.username, account.approved, account.role,
                song.year_id, song.poster_link,
                song.video_link, song.snippet_start, song.snippet_end,
                song.submitter_id, song.notes, song.sources
        FROM song
        JOIN country ON song.country_id = country.id
        LEFT OUTER JOIN account ON song.submitter_id = account.id
        LEFT OUTER JOIN language tl ON song.title_language_id = tl.id
        LEFT OUTER JOIN language nl ON song.native_language_id = nl.id
        WHERE (song.country_id = %(cc)s OR country.cc2 = %(cc)s) AND song.year_id IS NOT NULL
        ORDER BY song.year_id, country.name
    ''', {'cc': id})

    data = cursor.fetchall()

    res = [Song(
        id=d['id'],
        title=d['title'],
        artist=d['artist'],
        country=Country(id=d['country_id'],
                          cc2=d['cc2'],
                          name=d['name']),
        native_title=d['native_title'],
        year=d['year_id'],
        languages=get_languages(d['id']),
        placeholder=d['is_placeholder'],
        submitter=User(id=d['submitter_id'],
                       username=d['username'],
                       approved=d['approved'],
                       role=d['role']),
        translated_lyrics=d['translated_lyrics'],
        latin_lyrics=d['romanized_lyrics'],
        native_lyrics=d['native_lyrics'],
        lyrics_notes=d['notes'],
        video_link=d['video_link'],
        poster_link=d['poster_link'],
        sources=d['sources'],
        recap_start=format_seconds(d['snippet_start']) if d['snippet_start'] is not None else None,
        recap_end=format_seconds(d['snippet_end']) if d['snippet_end'] is not None else None,
        title_language=Language(
            name=d['t_name'],
            tag=d['t_tag'],
            extlang=d['t_extlang'],
            region=d['t_region'],
            subvariant=d['t_subvariant'],
            suppress_script=d['t_suppress_script']) if d['t_tag'] is not None else None,
        native_language=Language(
            name=d['n_name'],
            tag=d['n_tag'],
            extlang=d['n_extlang'],
            region=d['n_region'],
            subvariant=d['n_subvariant'],
            suppress_script=d['n_suppress_script']) if d['n_tag'] is not None else None,
        ) for d in data]

    return resp(res)