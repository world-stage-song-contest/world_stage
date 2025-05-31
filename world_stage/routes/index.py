from flask import Blueprint, request, send_file, make_response, current_app
from ..utils import create_cookie, parse_cookie, render_template
import os.path

bp = Blueprint('main', __name__, url_prefix='/')

@bp.get('/')
def home():
    return render_template('index.html', message="Welcome to the index page!")

@bp.get('/favicon.ico')
def favicon():
    return send_file('files/favicon.ico')

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

    resp = make_response(send_file(file))
    resp.headers['Content-Type'] = 'image/svg+xml'
    resp.cache_control.max_age = 60 * 60 * 24 * 30
    resp.cache_control.public = True
    resp.cache_control.immutable = True
    return resp