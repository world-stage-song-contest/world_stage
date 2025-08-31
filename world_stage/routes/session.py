from flask import Blueprint, request, make_response
import hashlib, datetime, os, unicodedata, uuid

from ..db import get_db
from ..utils import render_template

bp = Blueprint('session', __name__, url_prefix='/')

def hash_password(password: str) -> tuple[bytes, bytes]:
    salt = os.urandom(16)
    hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return hashed, salt

def verify_password(stored_password: bytes, stored_salt: bytes, provided_password: str) -> bool:
    hashed = hashlib.scrypt(provided_password.encode(), salt=stored_salt, n=16384, r=8, p=1)
    return stored_password == hashed

def validate_username(username: str) -> tuple[bool, str]:
    if not username:
        return False, "Username is required."
    if len(username) < 3:
        return False, "Username must be at least 3 characters long."
    if len(username) > 64:
        return False, "Username must be at most 64 characters long."
    return True, ""

def validate_password(password: str) -> tuple[bool, str]:
    if not password:
        return False, "Password is required."
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if len(password) > 64:
        return False, "Password must be at most 64 characters long."
    return True, ""

@bp.get('/login')
def login():
    if request.cookies.get('session'):
        return render_template('session/login_success.html', state="already_logged_in")

    username = request.cookies.get('username') or ''
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)

    if username:
        username = username.strip()
        username = unicodedata.normalize('NFKC', username)
    else:
        username = ""

    return render_template('session/login.html', username=username, message="Please log in to your account.")

@bp.post('/login')
def login_post():
    if request.cookies.get('session'):
        return render_template('session/login_success.html', state="already_logged_in")

    username = request.form.get('username', '')
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    password = request.form.get('password', '')

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template('session/login.html', message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template('session/login.html', message=password_message)

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, password, salt, approved FROM account WHERE username = %s', (username,))
    user = cursor.fetchone()
    if not user:
        return render_template('session/login.html', message="User not found.")

    if not user['password']:
        return render_template('session/login.html', message="You need to set a password first.")

    if not user['approved']:
        return render_template('session/login.html', message="Your account is not approved yet.")

    if not verify_password(user['password'], user['salt'], password):
        return render_template('session/login.html', message="Invalid password.")

    session_id = str(uuid.uuid4())
    cursor.execute('''
        INSERT INTO session (session_id, user_id, created_at, expires_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + '1 year')
    ''', (session_id, user['id']))

    db.commit()

    resp = make_response(render_template('session/login_success.html', state="success"))
    resp.set_cookie('session', session_id, max_age=datetime.timedelta(days=365))

    return resp

@bp.get('/setpassword')
def set_password():
    username = request.cookies.get('username') or ''
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    if username:
        username = username.strip()
        username = unicodedata.normalize('NFKC', username)
    else:
        username = ""

    return render_template('session/set_password.html', username=username)

@bp.post('/setpassword')
def set_password_post():
    username = request.form.get('username', '')
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    password = request.form.get('password', '')
    password2 = request.form.get('password2', '')

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template('session/set_password.html', message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template('session/set_password.html', message=password_message)
    if password != password2:
        return render_template('session/set_password.html', message="Passwords do not match.")

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, approved FROM account WHERE username = %s', (username,))
    user = cursor.fetchone()
    if not user:
        return render_template('session/set_password.html', message="User not found.")
    if not user['approved']:
        return render_template('session/set_password.html', message="Your account is not approved yet. Please ping a moderator.")
    hashed, salt = hash_password(password)
    cursor.execute('''
        UPDATE account
        SET password = %s, salt = %s
        WHERE id = %s
    ''', (hashed, salt, user['id']))

    db.commit()

    return render_template('session/set_password_success.html', state="success")

@bp.get('/signup')
def sign_up():
    username = request.cookies.get('username') or ''
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    if username:
        username = username.strip()
        username = unicodedata.normalize('NFKC', username)
    else:
        username = ""

    return render_template('session/request_account.html', username=username)

@bp.post('/signup')
def sign_up_post():
    username = request.form.get('username', '')
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    password = request.form.get('password', '')
    password2 = request.form.get('password2', '')

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template('session/request_account.html', message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template('session/request_account.html', message=password_message)
    if password != password2:
        return render_template('session/request_account.html', message="Passwords do not match.")

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id FROM account WHERE username = %s', (username,))
    user = cursor.fetchone()
    if user:
        return render_template('session/request_account.html', message="Your account already exists as you have either voted or submitted entries before. Instead of signing up, please <a href='/setpassword'>set your password</a>.")

    hashed, salt = hash_password(password)
    cursor.execute('''
        INSERT INTO account (username, password, salt, approved)
        VALUES (%s, %s, %s, 0)
    ''', (username, hashed, salt))

    db.commit()

    return render_template('session/request_account_success.html', state="success")

@bp.get("/logout")
def logout():
    return render_template('session/logout.html')

@bp.post("/logout")
def logout_post():
    session = request.cookies.get('session', '')
    if not session:
        return render_template('session/logout_success.html', state="not_logged_in")

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        DELETE FROM session WHERE session_id = %s
    ''', (session,))

    db.commit()

    resp = make_response(render_template('session/logout_success.html', state="logged_out"))
    resp.delete_cookie('session')
    return resp