from collections import defaultdict
from dataclasses import dataclass
import unicodedata
from flask import Blueprint, Response, redirect, request, url_for
from typing import Optional
import math

from ..db import get_db
from ..utils import LCG, get_user_id_from_session, format_seconds, get_user_role_from_session, get_year_countries, get_year_shows, get_year_songs, get_years, parse_seconds, render_template

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
    
    countries = get_year_countries(year, exclude=["ARG"])
    pots: dict[int, list[dict]] = defaultdict(list)
    for country in countries:
        pots[country['pot']].append(country)

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