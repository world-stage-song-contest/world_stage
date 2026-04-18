from flask import Blueprint, request, send_file, make_response, redirect, url_for, current_app
from ..db import get_db
from ..utils import (create_cookie, parse_cookie, render_template,
                     get_user_id_from_session, generate_api_token)
import os.path

# Lazy cache: 2-letter code → 3-letter code (populated on first miss).
_cc2_to_cc3: dict[str, str] = {}
_cc3_cache_loaded = False

def _ensure_cc3_cache():
    global _cc3_cache_loaded
    if _cc3_cache_loaded:
        return
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, cc3 FROM country')
    for row in cursor.fetchall():
        _cc2_to_cc3[row['id'].upper()] = row['cc3'].upper()
    _cc3_cache_loaded = True

def _get_cc3(cc2: str) -> str | None:
    """Return the 3-letter code for a 2-letter code, or None."""
    _ensure_cc3_cache()
    return _cc2_to_cc3.get(cc2.upper())

bp = Blueprint('main', __name__, url_prefix='/')

@bp.get('/')
def home():
    return render_template('index.html', message="Welcome to the index page!")

@bp.get('/favicon.ico')
def favicon():
    return send_file('files/favicons/favicon.ico')

@bp.get('/robots.txt')
def robots():
    return send_file('files/robots.txt')

@bp.get('/favicons/<name>')
def favicons(name: str):
    valid_names = ['favicon.ico', 'favicon-16x16.png', 'favicon-32x32.png', 'favicon.svg',
                   'apple-touch-icon.png', 'android-chrome-192x192.png', 'android-chrome-512x512.png',
                   'site.webmanifest']
    if name not in valid_names:
        return render_template('error.html', error="Invalid favicon name."), 404

    file_path = os.path.join(current_app.root_path, 'files', 'favicons', name)
    if not os.path.exists(file_path):
        return render_template('error.html', error="Favicon not found."), 404

    resp = make_response(send_file(file_path))
    match name:
        case 'favicon.ico':
            resp.headers['Content-Type'] = 'image/x-icon'
        case 'favicon-16x16.png' | 'favicon-32x32.png':
            resp.headers['Content-Type'] = 'image/png'
        case 'favicon.svg':
            resp.headers['Content-Type'] = 'image/svg+xml'
        case 'apple-touch-icon.png':
            resp.headers['Content-Type'] = 'image/png'
        case 'android-chrome-192x192.png' | 'android-chrome-512x512.png':
            resp.headers['Content-Type'] = 'image/png'
        case 'site.webmanifest':
            resp.headers['Content-Type'] = 'application/manifest+json'
    resp.cache_control.max_age = 60 * 60 * 24 * 30
    resp.cache_control.public = True
    resp.cache_control.immutable = True
    return resp

@bp.get('/error')
def error():
    errors = request.args.getlist('error')
    return render_template('error.html', errors=errors), 400

@bp.get('/error.json')
def error_json():
    errors = request.args.getlist('error')
    return {'errors': errors}, 400

def _get_user_tokens(user_id: int) -> list[dict]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, label, created_at, last_used_at
        FROM api_token WHERE user_id = %s
        ORDER BY created_at DESC
    ''', (user_id,))
    return cursor.fetchall()

@bp.get('/settings')
def settings():
    preferences = request.cookies.get('preferences', '')
    settings = parse_cookie(preferences)

    session_id = request.cookies.get('session')
    user = get_user_id_from_session(session_id) if session_id else None
    tokens = _get_user_tokens(user[0]) if user else []

    return render_template('settings.html', settings=settings,
                           user=user, tokens=tokens)

@bp.post('/settings')
def settings_post():
    settings = {}
    for key, value in request.form.items():
        if key not in ('token_label', 'delete_token'):
            settings[key] = value

    resp = make_response(render_template('settings.html', settings=settings, message="Settings saved successfully."))
    resp.set_cookie('preferences', create_cookie(**settings), max_age=60*60*24*30)
    return resp

@bp.post('/settings/token')
def create_token():
    session_id = request.cookies.get('session')
    user = get_user_id_from_session(session_id) if session_id else None
    if not user:
        return redirect(url_for('session.login'))

    user_id, _ = user
    label = request.form.get('token_label', '').strip()

    plaintext, token_hash = generate_api_token()

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO api_token (user_id, token_hash, label)
        VALUES (%s, %s, %s)
    ''', (user_id, token_hash, label))
    db.commit()

    preferences = request.cookies.get('preferences', '')
    settings = parse_cookie(preferences)
    tokens = _get_user_tokens(user_id)

    return render_template('settings.html', settings=settings,
                           user=user, tokens=tokens,
                           new_token=plaintext,
                           message="API token created. Copy it now — it won't be shown again.")

@bp.post('/settings/token/delete')
def delete_token():
    session_id = request.cookies.get('session')
    user = get_user_id_from_session(session_id) if session_id else None
    if not user:
        return redirect(url_for('session.login'))

    user_id, _ = user
    token_id = request.form.get('token_id', type=int)

    if token_id:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            DELETE FROM api_token WHERE id = %s AND user_id = %s
        ''', (token_id, user_id))
        db.commit()

    return redirect(url_for('main.settings'))

@bp.get('/flag')
def flag_index():
    return render_template('error.html', error="Do not access this page."), 401

@bp.get('/flag/<country>.svg')
def flag(country: str):
    country = country.upper()
    type = request.args.get('t', 'rect')

    if type not in ['rect', 'square']:
        type = 'rect'

    width = request.args.get('w', 40)
    try:
        width = int(width)
    except ValueError:
        width = 40

    size = ''
    if width <= 40:
        size = 'small'

    root = current_app.root_path
    flags_root = os.path.join(root, 'files', 'flags')

    # Try the code as-is first (2-letter), then fall back to the 3-letter
    # equivalent from the database, then to the XXX placeholder.
    candidates = [country]
    cc3 = _get_cc3(country)
    if cc3 and cc3 != country:
        candidates.append(cc3)

    file = None
    for candidate in candidates:
        path = os.path.join(flags_root, candidate, f'{type}-{size}.svg')
        if os.path.exists(path):
            file = path
            break
        path = os.path.join(flags_root, candidate, f'{type}.svg')
        if os.path.exists(path):
            file = path
            break

    if not file:
        file = os.path.join(flags_root, 'XX', f'{type}.svg')

    resp = make_response(send_file(file))
    resp.headers['Content-Type'] = 'image/svg+xml'
    resp.cache_control.max_age = 60 * 60 * 24 * 30
    resp.cache_control.public = True
    resp.cache_control.immutable = True
    return resp