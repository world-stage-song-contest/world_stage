from flask import Blueprint, render_template, request, make_response
import hashlib, datetime, os, unicodedata, uuid
from ..db import get_db

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
    username = request.args.get('username')

    if username:
        username = username.strip()
        username = unicodedata.normalize('NFKC', username)
    else:
        username = ""

    return render_template('session/login.html', username=username)

@bp.post('/login')
def login_post():
    if request.cookies.get('session'):
        return render_template('session/login_success.html', state="already_logged_in")
    
    username = request.form.get('username')
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    password = request.form.get('password')

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template('session/login.html', message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template('session/login.html', message=password_message)
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, password, salt, approved FROM user WHERE username = ?', (username,))
    user = cursor.fetchone()
    if not user:
        return render_template('session/login.html', message="User not found.")
    user_id, stored_password, stored_salt, is_approved = user

    if not stored_password:
        return render_template('session/login.html', message="You need to set a password first.")
    
    if not is_approved:
        return render_template('session/login.html', message="Your account is not approved yet.")

    if not verify_password(stored_password, stored_salt, password):
        return render_template('session/login.html', message="Invalid password.")
    
    session_id = str(uuid.uuid4())
    cursor.execute('''
        INSERT INTO session (session_id, user_id, created_at, expires_at)
        VALUES (?, ?, datetime('now'), datetime('now', '+30 days'))
    ''', (session_id, user_id))

    db.commit()

    resp = make_response(render_template('session/login_success.html', state="success"))
    resp.set_cookie('session', session_id, max_age=datetime.timedelta(days=30))

    return resp

@bp.get('/setpassword')
def set_password():
    if username:
        username = username.strip()
        username = unicodedata.normalize('NFKC', username)
    else:
        username = ""
    
    return render_template('session/set_password.html', username=username)

@bp.post('/setpassword')
def set_password_post():
    print(request.form)
    username = request.form.get('username')
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    password = request.form.get('password')
    password2 = request.form.get('password2')

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
    cursor.execute('SELECT id, password FROM user WHERE username = ?', (username,))
    user = cursor.fetchone()
    if not user:
        return render_template('session/set_password.html', message="User not found.")
    user_id, stored_password = user
    if stored_password:
        return render_template('session/set_password.html', message="Password already set.")
    hashed, salt = hash_password(password)
    cursor.execute('''
        UPDATE user
        SET password = ?, salt = ?
        WHERE id = ?
    ''', (hashed, salt, user_id))

    db.commit()

    return render_template('session/set_password_success.html', state="success")

@bp.get('/signup')
def signup():
    if username:
        username = username.strip()
        username = unicodedata.normalize('NFKC', username)
    else:
        username = ""

    return render_template('session/request_account.html', username=username)

@bp.post('/signup')
def signup_post():
    username = request.form.get('username')
    username = username.strip()
    username = unicodedata.normalize('NFKC', username)
    password = request.form.get('password')
    password2 = request.form.get('password2')

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
    cursor.execute('SELECT id FROM user WHERE username = ?', (username,))
    user = cursor.fetchone()
    if user:
        return render_template('session/request_account.html', message="User already exists.")
    
    hashed, salt = hash_password(password)
    cursor.execute('''
        INSERT INTO user (username, password, salt, approved)
        VALUES (?, ?, ?, 0)
    ''', (username, hashed, salt))
    
    db.commit()
    
    return render_template('session/request_account_success.html', state="success")
