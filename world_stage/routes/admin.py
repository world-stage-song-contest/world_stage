from collections import defaultdict
import sqlite3
from flask import Blueprint, redirect, request, url_for
import math
import datetime

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

@bp.get('/createshow')
def create_show():
    resp = verify_user()
    if resp:
        return resp
    return render_template('admin/create_show.html', years=get_years())

@bp.post('/createshow')
def create_show_post():
    resp = verify_user()
    if resp:
        return resp

    data = {}
    for key, value in request.form.items():
        try:
            value = int(value)
        except ValueError:
            pass

        if not value:
            value = None

        data[key] = value

    db = get_db()
    cur = db.cursor()

    cur.execute('''
        INSERT OR IGNORE INTO show (year_id, point_system_id, show_name, short_name, dtf, sc, date)
        VALUES (:year, 1, :show_name, :short_name, :dtf, :sc, :date)
    ''', data)
    db.commit()

    return redirect(url_for('admin.create_show'))

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
                    WHERE year_id = ? AND country_id = ?
                ''', (year, cc))
                song_id = cursor.fetchone()
                if not song_id:
                    return {'error': f'No song for country {cc} in year {year}'}
                song_id = song_id[0]

                cursor.execute('''
                    INSERT INTO song_show (song_id, show_id, running_order)
                    VALUES (?, ?, ?)
                ''', (song_id, show_data.id, i+1))
    except sqlite3.IntegrityError:
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

    countries = list(map(lambda s: s.country, songs))

    return render_template('admin/draw_individual.html', countries=countries, show=show, show_name=show_data.name, year=year, num=len(countries), lim=math.ceil((len(countries) / 2) or 1))

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
            WHERE year_id = ? AND country_id = ?
        ''', (year, cc))
        song_id = cursor.fetchone()
        if not song_id:
            return {'error': f'No song for country {cc} in year {year}'}
        song_id = song_id[0]

        cursor.execute('''
            UPDATE song_show
            SET running_order = ?
            WHERE song_id = ? AND show_id = ?
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
        JOIN user ON song.submitter_id = user.id
        WHERE modified_at >= datetime(date('now'), '-3 days')
        ORDER BY modified_at DESC
    ''')
    changes = [dict(row) for row in cursor.fetchall()]

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
    years = [row[0] for row in cursor.fetchall()]

    cursor.execute('''
        SELECT id, name FROM country ORDER BY name
    ''')
    countries = [dict(row) for row in cursor.fetchall()]

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
    years = [row[0] for row in cursor.fetchall()]

    cursor.execute('''
        SELECT id, name FROM country ORDER BY name
    ''')
    countries = [dict(row) for row in cursor.fetchall()]

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
            SET year_id = COALESCE(?, year_id),
                country_id = COALESCE(?, country_id)
            WHERE year_id = ? AND country_id = ?
        ''', (to_year, to_cc, from_year, from_cc))
    except sqlite3.IntegrityError as e:
        return render_template('admin/move.html', error=f"Database error: {str(e)}",
                               from_year=from_year_txt, to_year=to_year_txt,
                               from_cc=from_cc, to_cc=to_cc,
                               years=years, countries=countries), 400
    db.commit()
    return render_template('admin/move.html', message="Songs moved successfully.",
                           years=years, countries=countries)

@bp.get('/manage/<int:year>')
def manage(year: int):
    resp = verify_user()
    if resp:
        return resp

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT show_name, short_name, date, allow_access_type FROM show WHERE year_id = ?
    ''', (year,))
    shows = [dict(r) for r in cursor.fetchall()]

    return render_template('admin/manage_shows.html', year=year, shows=shows)

@bp.post('/manage/<int:year>/<show>')
def manage_post(year: int, show: str):
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
                SET voting_opens = datetime('now')
                WHERE year_id = ? AND short_name = ?
            ''', (year, show))
        case 'close_voting':
            cursor.execute('''
                UPDATE show
                SET voting_closes = datetime('now')
                WHERE year_id = ? AND short_name = ?
            ''', (year, show))
        case 'set_access_type':
            access_type = body.get('access_type')
            if access_type not in ['none', 'draw', 'partial', 'full']:
                return render_template('error.html', error="Invalid access type"), 400

            cursor.execute('''
                UPDATE show
                SET allow_access_type = ?
                WHERE year_id = ? AND short_name = ?
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
                SET date = ?
                WHERE year_id = ? AND short_name = ?
            ''', (date, year, show))
        case _:
            return render_template('error.html', error=f"Unknown action '{action}'"), 400

    db.commit()

    return {'status': 'success'}, 200

@bp.get('/fuckupdb')
def fuckup_db():
    resp = verify_user()
    if resp:
        return resp

    return render_template('admin/fuckupdb.html')

@bp.post('/fuckupdb')
def fuckup_db_post():
    resp = verify_user()
    if resp:
        return render_template('error.html', error="Not an admin"), 401

    db = get_db()
    cursor = db.cursor()

    body = request.get_json()
    if not body:
        return render_template('error.html', error="Empty request body"), 400

    query = body.get('query')
    if not query:
        return render_template('error.html', error="No query provided"), 400

    backup_path = f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db.execute("PRAGMA database_list").fetchone()[2], backup_path)

    try:
        cursor.execute(query)
        db.commit()
    except Exception as e:
        return render_template('error.html', error=f"Query failed: {str(e)}"), 400

    data = cursor.fetchall()
    headers = [description[0] for description in cursor.description] if cursor.description else []
    rows = [dict(row) for row in data]

    return {'headers': headers, 'rows': rows}, 200