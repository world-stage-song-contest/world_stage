from flask import Blueprint, request, send_file, make_response, current_app
from ..utils import create_cookie, parse_cookie, render_template
import os.path

bp = Blueprint('main', __name__, url_prefix='/')

@bp.get('/')
def home():
    return render_template('index.html', message="Welcome to the index page!")

@bp.get('/favicon.ico')
def favicon():
    return send_file('files/favicons/favicon.ico')

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

@bp.get('/settings')
def settings():
    preferences = request.cookies.get('preferences', '')
    settings = parse_cookie(preferences)

    return render_template('settings.html', settings=settings)

@bp.post('/settings')
def settings_post():
    settings = {}
    for key, value in request.form.items():
        settings[key] = value

    resp = make_response(render_template('settings.html', settings=settings, message="Settings saved successfully."))
    resp.set_cookie('preferences', create_cookie(**settings), max_age=60*60*24*30)
    return resp

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
    file = os.path.join(root, 'files', 'flags', country, f'{type}-{size}.svg')
    if not os.path.exists(file):
        file = os.path.join(root, 'files', 'flags', country, f'{type}.svg')
    if not os.path.exists(file):
        file = os.path.join(root, 'files', 'flags', 'XXX', f'{type}.svg')

    resp = make_response(send_file(file))
    resp.headers['Content-Type'] = 'image/svg+xml'
    resp.cache_control.max_age = 60 * 60 * 24 * 30
    resp.cache_control.public = True
    resp.cache_control.immutable = True
    return resp