from collections import defaultdict
import sqlite3
from flask import Blueprint, redirect, request, url_for
import math

from ..db import get_db
from ..utils import LCG, get_show_id, get_show_songs, get_user_role_from_session, get_year_countries, get_year_shows, get_years, render_template

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
        INSERT OR IGNORE INTO show (year_id, point_system_id, show_name, short_name, dtf, sc)
        VALUES (:year, 1, :show_name, :short_name, :dtf, :sc)
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

    cursor.execute('''
        UPDATE song
        SET year_id = COALESCE(?, year_id),
            country_id = COALESCE(?, country_id)
        WHERE year_id = ? AND country_id = ?
    ''', (to_year, to_cc, from_year, from_cc))
    db.commit()
    return render_template('admin/move.html', message="Songs moved successfully.",
                           years=years, countries=countries)