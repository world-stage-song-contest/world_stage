from flask import Blueprint, Response, current_app, redirect, request, url_for

from world_stage.models import Country, Language, Song, User, Year

from world_stage.db import get_db
from world_stage.utils import ErrorID, err, format_seconds, resp, url_bool

bp = Blueprint('year', __name__, url_prefix='/year')

@bp.get('/')
def index():
    kind = request.args.get('type', '')

    match kind:
        case 'open' | 'closed' | 'ongoing':
            status = kind
        case _:
            status = None

    db = get_db()
    cursor = db.cursor()

    if status is not None:
        cursor.execute("""
SELECT year.id, year.status, year.host_id, country.name, country.cc3
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
WHERE year.status = %s
ORDER BY year.id
""", (status,))
    else:
        cursor.execute("""
SELECT year.id, year.status, year.host_id, country.name, country.cc3
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
ORDER BY year.id
""")

    data = [Year(year=val['id'],
                 status=val['status'],
                 host=Country(id=val['host_id'],
                              cc3=val['cc3'],
                              name=val['name']) if val['host_id'] is not None else None).to_json()
            for val in cursor.fetchall()]
    return resp(data)

@bp.get('/<int:id>')
def year(id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
SELECT year.id, year.status, year.host_id, country.name, country.cc3,
       COUNT(song.is_placeholder) FILTER(WHERE song.is_placeholder = false) AS entries,
       COUNT(song.is_placeholder) FILTER(WHERE song.is_placeholder = true) AS placeholders
FROM year
LEFT OUTER JOIN country ON year.host_id = country.id
JOIN song ON song.year_id = year.id
WHERE year.id = %s
GROUP BY year.id, country.id
ORDER BY year.id
""", (id,))

    val = cursor.fetchone()
    if not val:
        return err(ErrorID.NOT_FOUND, f"Country {id} not found")

    data = Year(year=val['id'],
                status=val['status'],
                entry_count=val['entries'],
                placeholder_count=val['placeholders'],
                host=Country(id=val['host_id'],
                             cc3=val['cc3'],
                             name=val['name']) if val['host_id'] is not None else None).to_json()
    return resp(data)

@bp.get('/<int:id>/songs')
def songs(id: int):
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
                song.country_id, country.name, country.cc3, song.is_placeholder,
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
        WHERE song.year_id IS NOT NULL AND song.year_id = %(year)s
        ORDER BY song.year_id, country.name
    ''', {'year': id})

    data = cursor.fetchall()

    res = [Song(
        id=d['id'],
        title=d['title'],
        artist=d['artist'],
        country=Country(id=d['country_id'],
                          cc3=d['cc3'],
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