from flask import Blueprint, redirect, render_template, request, url_for

from ..db import get_db

bp = Blueprint('user', __name__, url_prefix='/user')

@bp.get('/')
def user_index():
    session_id = request.cookies.get('session')
    if not session_id:
        return redirect(url_for('session.login'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT user.username, user.id
        FROM session
        JOIN user ON session.user_id = user.id
        WHERE session_id = ?
    ''', (session_id,))

    username, user_id = cursor.fetchone()

    return render_template('user/index.html', username=username)

@bp.get('/submit')
def submit():
    return render_template('user/submit.html')