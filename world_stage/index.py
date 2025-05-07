from flask import Blueprint, render_template, request, send_file

bp = Blueprint('main', __name__, url_prefix='/')

@bp.get('/')
def home():
    return render_template('home.html')

@bp.get('/success')
def success():
    action = request.args.get('action')
    return render_template('successfully_voted.html', action=action)

@bp.get('/favicon.ico')
def favicon():
    return send_file('files/favicon.ico')

@bp.get('/error')
def error():
    error = request.args.get('error')
    return render_template('error.html', error=error), 400

@bp.get('/error.json')
def error_json():
    error = request.args.get('error')
    return {'error': error}, 400