from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import subprocess
from typing import LiteralString, Sequence, Callable, Any
import psycopg
from flask import Blueprint, Response, current_app, redirect, request, url_for
import math
import datetime
import csv, io

from ..db import get_db
from ..utils import LCG, get_show_id, get_show_songs, get_user_role_from_session, get_year_countries, get_year_shows, get_years, render_template
import shutil
import os

bp = Blueprint('admin', __name__, url_prefix='/admin')

def verify_user():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect('/')
    permissions = get_user_role_from_session(session_id)
    if not permissions.can_view_restricted:
        return redirect('/')
    return None

@bp.get('/')
def index():
    resp = verify_user()
    if resp:
        return resp
    return render_template('admin/index.html')

@bp.get('/manage/<int:year>/createshow')
def create_show(year: int):
    resp = verify_user()
    if resp:
        return resp
    return render_template('admin/create_show.html', years=get_years(), year=year)

@bp.post('/manage/<int:year>/createshow')
def create_show_post(year: int):
    resp = verify_user()
    if resp:
        return resp

    data: dict[str, int | str | None] = {'year': year}
    value: int | str | None
    for key, value_ in request.form.items():
        value = value_
        try:
            value = int(value)
        except ValueError:
            pass

        if not value:
            value = None

        data[key] = value

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute('''
            INSERT INTO show (year_id, point_system_id, show_name, short_name, dtf, sc, date, access_type)
            VALUES (%(year)s, 1, %(show_name)s, %(short_name)s, %(dtf)s, %(sc)s, %(date)s, 'none')
        ''', data)
        db.commit()
    except psycopg.Error as e:
        return render_template('admin/create_show.html', error=str(e))

    return redirect(url_for('admin.create_show', year=year))

@bp.get('/draw/<int:year>')
def draw(year: int):
    resp = verify_user()
    if resp:
        return resp

    countries = get_year_countries(year)
    pots_raw: dict[int, list[dict]] = defaultdict(list)
    for country in countries:
        pot: int | None = country.get('pot', None)
        if pot is not None:
            pots_raw[pot].append(country)

    pots: dict[int, list[dict]] = {}
    semifinalists = 0
    for k in sorted(pots_raw.keys()):
        pots[k] = pots_raw[k]
        semifinalists += len(pots[k])

    shows = get_year_shows(year, pattern='sf')
    count = len(shows)
    if count == 0:
        return render_template('error.html', error=f"No semifinal shows found for {year}"), 404
    per = semifinalists // count
    songs = [per] * count
    deficit = semifinalists - per * count
    lcg = LCG(year)
    for i in range(deficit):
        songs[i] += 1

    limits = list(map(lambda n: math.ceil(n / 2), songs))

    return render_template('admin/draw.html', pots=pots, shows=shows, songs=songs, limits=limits, year=year)

@bp.post('/draw/<int:year>')
def draw_post(year: int):
    resp = verify_user()
    if resp:
        return {'error': "Not an admin"}, 401

    data: dict[str, list[str]] | None = request.json
    if not data:
        return {'error': "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    try:
        for show, ro in data.items():
            show_data = get_show_id(show, year)
            if not show_data:
                return {'error': f"Invalid show {show} for {year}"}, 400

            for i, cc in enumerate(ro):
                cursor.execute('''
                    SELECT id FROM song
                    WHERE year_id = %s AND country_id = %s
                ''', (year, cc))
                song_id = cursor.fetchone()
                if not song_id:
                    return {'error': f'No song for country {cc} in year {year}'}
                song_id = song_id['id']

                cursor.execute('''
                    INSERT INTO song_show (song_id, show_id, running_order)
                    VALUES (%s, %s, %s)
                ''', (song_id, show_data.id, i+1))
    except psycopg.IntegrityError:
        return {'error': "Duplicate data"}, 400

    db.commit()
    return {}, 204

@bp.get('/draw/<int:year>/<show>')
def draw_final(year: int, show: str):
    resp = verify_user()
    if resp:
        return resp

    show_data = get_show_id(show, year)
    if not show_data:
        return render_template('error.html', error=f"Invalid show '{show}' for {year}"), 404

    songs = get_show_songs(year, show, sort_reveal=True)

    if not songs:
        return render_template('error.html', error="No show '{show}' found for {year}"), 404

    return render_template('admin/draw_individual.html', songs=songs, show=show, show_name=show_data.name, year=year, num=len(songs), lim=math.ceil((len(songs) / 2) or 1))

@bp.post('/draw/<int:year>/<show>')
def draw_final_post(year: int, show: str):
    resp = verify_user()
    if resp:
        return {'error': "Not an admin"}, 401

    data: dict[str, list[str]] | None = request.json
    if not data:
        return {'error': "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    show_data = get_show_id(show, year)
    if not show_data:
        return {'error': f"Invalid show '{show}' for {year}"}, 400

    ro = data.get(show)
    if ro is None:
        return {'error': "No running order provided"}, 400

    for i, cc in enumerate(ro):
        cursor.execute('''
            SELECT id FROM song
            WHERE year_id = %s AND country_id = %s
        ''', (year, cc))
        song_id = cursor.fetchone()
        if not song_id:
            return {'error': f'No song for country {cc} in year {year}'}
        song_id = song_id['id']

        cursor.execute('''
            UPDATE song_show
            SET running_order = %s
            WHERE song_id = %s AND show_id = %s
        ''', (i+1, song_id, show_data.id))

    db.commit()
    return {}, 204

@bp.get('/changes')
def changes():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT country_id AS cc, country.name, year_id AS year, artist, title, username AS submitter, modified_at FROM song
        JOIN country ON song.country_id = country.id
        JOIN account ON song.submitter_id = account.id
        WHERE modified_at >= CURRENT_TIMESTAMP - interval '1 day'
        ORDER BY modified_at DESC
    ''')
    changes = cursor.fetchall()

    return render_template('admin/changes.html', changes=changes)

@bp.get('/move')
def move():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM year ORDER BY id
    ''')
    years = cursor.fetchall()

    cursor.execute('''
        SELECT id, name FROM country WHERE is_participating ORDER BY name
    ''')
    countries = cursor.fetchall()

    return render_template('admin/move.html', years=years, countries=countries)

@bp.post('/move')
def move_post():
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM year ORDER BY id
    ''')
    years = cursor.fetchall()

    cursor.execute('''
        SELECT id, name FROM country WHERE is_participating ORDER BY name
    ''')
    countries = cursor.fetchall()

    from_year_txt = request.form.get('from_year')
    to_year_txt = request.form.get('to_year')

    from_cc = request.form.get('from_cc')
    to_cc = request.form.get('to_cc')

    if not from_year_txt or not from_cc:
        return render_template('admin/move.html', error="From year and from country must be specificed",
                               from_year=from_year_txt, to_year=to_year_txt,
                               from_cc=from_cc, to_cc=to_cc,
                               years=years, countries=countries), 400

    if not to_year_txt and not to_cc:
        return render_template('admin/move.html', error="At least one of to year and to country must be specificed",
                               from_year=from_year_txt, to_year=to_year_txt,
                               from_cc=from_cc, to_cc=to_cc,
                               years=years, countries=countries), 400

    try:
        from_year = int(from_year_txt)
    except ValueError:
        return render_template('admin/move.html', error="Invalid from year",
                               from_year=from_year_txt, to_year=to_year_txt,
                               from_cc=from_cc, to_cc=to_cc,
                               years=years, countries=countries), 400

    try:
        to_year = int(to_year_txt) if to_year_txt else None
    except ValueError:
        return render_template('admin/move.html', error="Invalid to year",
                               from_year=from_year_txt, to_year=to_year_txt,
                               from_cc=from_cc, to_cc=to_cc,
                               years=years, countries=countries), 400

    try:
        cursor.execute('''
            UPDATE song
            SET year_id = COALESCE(%s, year_id),
                country_id = COALESCE(%s, country_id)
            WHERE year_id = %s AND country_id = %s
        ''', (to_year, to_cc, from_year, from_cc))
    except psycopg.Error as e:
        return render_template('admin/move.html', error=f"Database error: {str(e)}",
                               from_year=from_year_txt, to_year=to_year_txt,
                               from_cc=from_cc, to_cc=to_cc,
                               years=years, countries=countries), 400
    db.commit()
    return render_template('admin/move.html', message="Songs moved successfully.",
                           years=years, countries=countries)

@bp.get('/manage')
def manage_index():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id, closed FROM year ORDER BY id')
    years = cursor.fetchall()

    return render_template('admin/manage_index.html', years=years)

@bp.get('/manage/<int:year>')
def manage(year: int):
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, closed FROM year WHERE id = %s
    ''', (year,))
    year_data = cursor.fetchone()
    if not year_data:
        return render_template('error.html', error=f"Year {year} not found"), 404

    cursor.execute('''
        SELECT show_name, short_name, date, access_type FROM show WHERE year_id = %s
        ORDER BY id
    ''', (year,))
    shows = cursor.fetchall()

    return render_template('admin/manage_shows.html', year=year_data, shows=shows)

@bp.post('/manage/<int:year>')
def manage_post(year: int):
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    body = request.get_json()
    if not body:
        return render_template('error.html', error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    action = body.get('action')
    if not action:
        return render_template('error.html', error="No action specified"), 400

    match action:
        case 'change_year_status':
            closed = body.get('year_status')
            if closed is None:
                return render_template('error.html', error="No closed status provided"), 400

            cursor.execute('''
                UPDATE year
                SET closed = %s
                WHERE id = %s
            ''', (closed, year))
        case _:
            return render_template('error.html', error=f"Unknown action '{action}'"), 400
    db.commit()
    return {'status': 'success'}, 200

@bp.post('/manage/<int:year>/<show>')
def manage_show_post(year: int, show: str):
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    body = request.get_json()
    if not body:
        return render_template('error.html', error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    action = body.get('action')
    if not action:
        return render_template('error.html', error="No action specified"), 400

    match action:
        case 'open_voting':
            cursor.execute('''
                UPDATE show
                SET voting_opens = COALESCE(voting_opens, CURRENT_TIMESTAMP)
                  , voting_closes = NULL
                WHERE year_id = %s AND short_name = %s
            ''', (year, show))
        case 'close_voting':
            cursor.execute('''
                UPDATE show
                SET voting_closes = CURRENT_TIMESTAMP
                WHERE year_id = %s AND short_name = %s
            ''', (year, show))
        case 'set_access_type':
            access_type = body.get('access_type')
            if access_type not in ['none', 'draw', 'partial', 'full']:
                return render_template('error.html', error="Invalid access type"), 400

            cursor.execute('''
                UPDATE show
                SET access_type = %s
                WHERE year_id = %s AND short_name = %s
            ''', (access_type, year, show))
        case 'change_date':
            date_str = body.get('date')
            if not date_str:
                return render_template('error.html', error="No date provided"), 400
            try:
                date = datetime.date.fromisoformat(date_str)
            except ValueError:
                return render_template('error.html', error="Invalid date format"), 400
            if not date:
                return render_template('error.html', error="Invalid date format"), 400

            cursor.execute('''
                UPDATE show
                SET date = %s
                WHERE year_id = %s AND short_name = %s
            ''', (date, year, show))
        case _:
            return render_template('error.html', error=f"Unknown action '{action}'"), 400

    db.commit()

    return {'status': 'success'}, 200

@bp.get('/fuckupdb')
def fuckup_db():
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    return render_template('admin/fuckupdb.html')

@bp.post('/fuckupdb')
def fuckup_db_post():
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    db = get_db()
    cursor = db.cursor()

    query = request.form.get('query')
    if not query:
        return render_template('admin/fuckupdb.html', error="No query provided"), 400

    subprocess.run(os.environ["BACKUP_SCRIPT"])

    try:
        cursor.execute("SET ROLE dml_only_role")
        cursor.execute(query) # type: ignore
        db.commit()

        rows = []
        headers = []
        if cursor.description is not None:
            rows = cursor.fetchall()
            headers = [description[0] for description in cursor.description] if cursor.description else []

        cursor.execute("RESET ROLE")
    except psycopg.Error as e:

        db.rollback()
        return render_template('admin/fuckupdb.html', error=f"Query failed: {str(e)}", query=query), 400

    kind = request.form.get('kind')
    if kind == 'csv':
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

        filename = datetime.datetime.now(tz=datetime.UTC).strftime("query_%Y%m%dT%H%M%SZ.csv")

        response = Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
        return response
    elif kind == 'html':
        return render_template('admin/fuckupdb.html', rows=rows, headers=headers, query=query)
    else:
        return render_template('admin/fuckupdb.html', error=f"Unknown filetype: {kind}"), 400

@bp.get('/users')
def users():
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, username, approved, role FROM account
    ''')
    users = cursor.fetchall()

    return render_template('admin/users.html', users=users)

@bp.post('/users')
def users_post():
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    body = request.get_json()
    if not body:
        return render_template('error.html', error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    user_id = body.get('user_id')
    action = body.get('action')

    if not user_id or not action:
        return render_template('error.html', error="User ID and action must be provided"), 400

    if action == 'approve':
        cursor.execute('''
            UPDATE account
            SET approved = 1
            WHERE id = %s
        ''', (user_id,))
    elif action == 'unapprove':
        cursor.execute('''
            UPDATE account
            SET approved = 0
            WHERE id = %s
        ''', (user_id,))
    elif action == 'annul_password':
        cursor.execute('''
            UPDATE account
            SET password = NULL, salt = NULL
            WHERE id = %s
        ''', (user_id,))
    else:
        return render_template('error.html', error=f"Unknown action '{action}'"), 400

    db.commit()

    return {'status': 'success'}, 200

@bp.get('/manage/<int:year>/setpots')
def set_pots(year: int):
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT country.id, name, pot FROM song
        JOIN country ON song.country_id = country.id
        JOIN year ON song.year_id = year.id
        WHERE year_id = %s AND year.host IS DISTINCT FROM country.id
        ORDER BY pot, name
    ''', (year,))
    countries = cursor.fetchall()

    return render_template('admin/set_pots.html', countries=countries, year=year)

@bp.post('/manage/<int:year>/setpots')
def set_pots_post(year: int):
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    db = get_db()
    cursor = db.cursor()

    cursor.execute('UPDATE country SET pot = NULL')

    for country_id, pot_str in request.form.items():
        try:
            pot: int | None = int(pot_str)
            if pot == 0:
                pot = None
        except ValueError:
            return render_template('error.html', error=f"Invalid priority value for country {country_id}"), 400

        cursor.execute('''
            UPDATE country
            SET pot = %s
            WHERE id = %s
        ''', (pot, country_id))

    db.commit()
    return redirect(url_for('admin.set_pots', year=year))

@bp.get('/upload')
def upload():
    resp = verify_user()
    if resp:
        return resp

    return render_template('admin/upload.html')

@bp.post('/upload')
def upload_post():
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    file = request.files.get('file')
    if not file:
        return render_template('error.html', error="No file uploaded"), 400

    file_path = Path(current_app.instance_path, 'uploads', file.filename or datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.dat')
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file.save(file_path)

    return render_template('admin/upload.html', message=f"File '{file.filename}' uploaded successfully.", file_path=str(file_path))

@bp.get('/recapdata')
def recap_data():
    resp = verify_user()
    if resp:
        return resp

    return render_template('admin/recap_data.html')

class BadRecapRequest(ValueError):
    pass

def _require(condition: bool, msg: str) -> None:
    if not condition:
        raise BadRecapRequest(msg)

def _parse_year(s: str) -> int:
    _require(len(s) == 4 and s.isdigit(), f"Year '{s}' is invalid. It needs to have exactly four digits.")
    return int(s)

def _parse_cc2(s: str) -> str:
    _require(len(s) == 2, f"Code '{s}' is invalid. It needs to have exactly two characters.")
    return s

def _parse_show_key(s: str) -> tuple[int, str]:
    try:
        year_s, short_name = s.split("-", 1)
    except ValueError:
        raise BadRecapRequest(f"Invalid number of dashes in the value '{s}'")

    year = _parse_year(year_s)
    _require(bool(short_name), f"Invalid show name '{short_name}' in show '{s}'")
    return year, short_name

def _lookup_many(
    cursor,
    sql: LiteralString,
    params_list: Sequence[tuple[Any, ...]],
    *,
    not_found_msg: Callable[[tuple[Any, ...]], str],
) -> list[int]:
    out: list[int] = []
    for params in params_list:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if not row:
            raise BadRecapRequest(not_found_msg(params))
        out.append(row["id"])
    return out

@dataclass(frozen=True)
class Spec:
    parse: Callable[[list[str], Any], Any]   # (form_data, cursor) -> param
    sql: LiteralString

def _parse_show_ids(form_data: list[str], cursor) -> list[int]:
    keys = [_parse_show_key(s) for s in form_data]
    return _lookup_many(
        cursor,
        "SELECT id FROM show WHERE year_id = %s AND short_name = %s",
        keys,
        not_found_msg=lambda p: f"Show '{p[0]}-{p[1]}' not found",
    )

def _parse_years(form_data: list[str], cursor) -> list[int]:
    return [_parse_year(s) for s in form_data]

def _parse_countries(form_data: list[str], cursor) -> list[str]:
    return [_parse_cc2(s) for s in form_data]

def _parse_submitter_ids(form_data: list[str], cursor) -> list[int]:
    params_list = [(u,) for u in form_data]
    return _lookup_many(
        cursor,
        "SELECT id FROM account WHERE username = %s",
        params_list,
        not_found_msg=lambda p: f"User '{p[0]}' is unknown",
    )

_SQL_SHOW = """
WITH song_data AS (
    SELECT DISTINCT ON (song.id, show.id)
           show.id as show_id, show.year_id || short_name AS show, running_order AS ro,
           LOWER(cc2) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song_show
    JOIN song ON song_show.song_id = song.id
    JOIN show ON song_show.show_id = show.id
    JOIN country ON song.country_id = country.id
    WHERE show.id = ANY(%s)
    ORDER BY song.id, show.id
)
SELECT show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY show_id, ro
"""

_SQL_YEAR = """
WITH song_data AS (
    SELECT song.year_id as show_id, song.year_id AS show, UPPER(cc2) AS ro,
           LOWER(cc2) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    WHERE song.year_id = ANY(%s)
    ORDER BY LOWER(cc2)
)
SELECT show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY country
"""

_SQL_COUNTRY = """
WITH song_data AS (
    SELECT song.year_id AS year, LOWER(cc2) as show_id, LOWER(cc2) AS show,
           MOD(song.year_id, 100) AS ro,
           LOWER(cc2) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    JOIN year ON song.year_id = year.id
    WHERE country.cc2 = ANY(%s) AND year_id IS NOT NULL AND year.closed = 1
    ORDER BY song.year_id
)
SELECT show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY year
"""

_SQL_SUBMITTER = """
WITH song_data AS (
    SELECT account.username as show_id, song.year_id AS year, account.username AS show,
           MOD(song.year_id, 100) AS ro,
           LOWER(cc2) AS cc, country.name AS country,
           artist, title, video_link AS media_link, snippet_start, snippet_end,
           poster_link AS image_link,
           (SELECT STRING_AGG(COALESCE(l.code3, l.tag), ', ')
            FROM song_language sl
            JOIN language l ON sl.language_id = l.id
            WHERE sl.song_id = song.id) AS language,
           CASE WHEN poster_link IS NULL THEN 'video' ELSE 'audio' END AS type
    FROM song
    JOIN country ON song.country_id = country.id
    JOIN year ON song.year_id = year.id
    JOIN account ON song.submitter_id = account.id
    WHERE song.submitter_id = ANY(%s) AND year_id IS NOT NULL AND year.closed = 1
    ORDER BY song.year_id, country
)
SELECT year, show, ro, cc, country,
       artist, title, media_link, snippet_start, snippet_end, language,
       type, image_link
FROM song_data
ORDER BY year, country
"""

_SPECS: dict[str, Spec] = {
    "show": Spec(parse=_parse_show_ids, sql=_SQL_SHOW),
    "year": Spec(parse=_parse_years, sql=_SQL_YEAR),
    "country": Spec(parse=_parse_countries, sql=_SQL_COUNTRY),
    "submitter": Spec(parse=_parse_submitter_ids, sql=_SQL_SUBMITTER),
}

def get_recap_data(mode: str, form_data: list[str]) -> list[dict] | None:
    db = get_db()
    cursor = db.cursor()

    spec = _SPECS.get(mode)
    if not spec:
        return None

    try:
        param = spec.parse(form_data, cursor)
        cursor.execute(spec.sql, (param,))
        return cursor.fetchall()
    except BadRecapRequest:
        return None

def drop_none(obj):
    if isinstance(obj, dict):
        return {k: drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [drop_none(v) for v in obj]
    return obj

@bp.post('/recapdata')
def recap_data_post() -> Response | tuple[Response, int]:
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    form_data = request.form.getlist('show')
    type = request.form.get('type', '')
    action = request.form.get('action', 'render')
    pretty = request.form.get('pretty', 'off') == 'on'
    if pretty:
        indent = 2
    else:
        indent = None

    data = get_recap_data(type, form_data)
    if data is None:
        return render_template('error.html', error="An error has occured")

    json_data = json.dumps(drop_none(data), ensure_ascii=False, indent=indent)

    if action == 'render':
        return render_template('admin/recap_data.html', data=json_data)
    elif action == 'download':
        response = Response(
            json_data,
            mimetype='application/json; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename={'+'.join(form_data)}.json',
            }
        )
        return response
    else:
        return render_template('error.html', error=f"Unknown action: {action}")