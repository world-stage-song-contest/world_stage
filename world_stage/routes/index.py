from flask import Blueprint, render_template, request, send_file, make_response
from ..utils import create_cookie, parse_cookie

bp = Blueprint('main', __name__, url_prefix='/')

@bp.get('/')
def home():
    return render_template('index.html')

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