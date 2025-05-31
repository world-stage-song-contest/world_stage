from collections import defaultdict
from dataclasses import dataclass
import json
import sqlite3
import unicodedata
from flask import Blueprint, Response, redirect, request, url_for
from typing import Optional
import math

from ..db import get_db
from ..utils import LCG, get_show_id, get_show_songs, get_user_id_from_session, format_seconds, get_user_role_from_session, get_year_countries, get_year_shows, get_year_songs, get_years, parse_seconds, render_template

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
        INSERT OR IGNORE INTO show (year_id, point_system_id, show_name, short_name, dtf)
        VALUES (:year, 1, :show_name, :short_name, :dtf)
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
        pots_raw[country['pot']].append(country)

    pots: dict[int, list[dict]] = {}
    for k in sorted(pots_raw.keys()):
        pots[k] = pots_raw[k]

    shows = get_year_shows(year, pattern='sf')
    count = len(shows)
    semifinalists = len(countries)
    per = semifinalists // count
    songs = [per] * count
    deficit = semifinalists - per * count
    lcg = LCG(year)
    for _ in range(deficit):
        n = lcg.next() % count
        songs[n] += 1

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

@bp.get('/draw/<int:year>/final')
def draw_final(year: int):
    resp = verify_user()
    if resp:
        return resp

    songs = get_show_songs(year, 'f')

    if not songs:
        return render_template('error.html', error="No final show found for this year"), 404
    countries = list(map(lambda s: s.country, songs))

    return render_template('admin/draw_final.html', countries=countries, show='f', year=year, num=len(countries), lim=(len(countries) // 2) or 1)

@bp.post('/draw/<int:year>/final')
def draw_final_post(year: int):
    resp = verify_user()
    if resp:
        return {'error': "Not an admin"}, 401

    data: dict[str, list[str]] | None = request.json
    if not data:
        return {'error': "Empty request"}, 400

    db = get_db()
    cursor = db.cursor()

    show_data = get_show_id('f', year)
    if not show_data:
        return {'error': f"Invalid final show for {year}"}, 400

    ro = data.get('f')
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