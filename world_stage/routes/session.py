import datetime
import hashlib
import os
import secrets
import smtplib
import unicodedata
import uuid

from flask import Blueprint, Response, current_app, make_response, redirect, request, url_for

from ..db import get_db
from ..email import external_url, is_configured, send_email, validate_email
from ..utils import get_user_id_from_session, render_template

bp = Blueprint("session", __name__, url_prefix="/")


def _existing_session_response() -> Response | None:
    session_id = request.cookies.get("session")
    if not session_id:
        return None

    if get_user_id_from_session(session_id):
        return make_response(
            render_template("session/login_success.html", state="already_logged_in")
        )

    response = redirect(url_for("session.login"))
    response.delete_cookie("session")
    return response


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


@bp.get("/login")
def login():
    if response := _existing_session_response():
        return response

    username = request.cookies.get("username") or ""
    username = username.strip()
    username = unicodedata.normalize("NFKC", username)

    if username:
        username = username.strip()
        username = unicodedata.normalize("NFKC", username)
    else:
        username = ""

    return render_template(
        "session/login.html", username=username, message="Please log in to your account."
    )


@bp.post("/login")
def login_post():
    if response := _existing_session_response():
        return response

    username = request.form.get("username", "")
    username = username.strip()
    username = unicodedata.normalize("NFKC", username)
    password = request.form.get("password", "")

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template("session/login.html", message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template("session/login.html", message=password_message)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, password, salt, approved FROM account WHERE LOWER(username) = LOWER(%s)",
        (username,),
    )
    user = cursor.fetchone()
    if not user:
        return render_template("session/login.html", message="User not found.")

    if not user["password"]:
        return render_template("session/login.html", message="You need to set a password first.")

    if not user["approved"]:
        return render_template("session/login.html", message="Your account is not approved yet.")

    if not verify_password(user["password"], user["salt"], password):
        return render_template("session/login.html", message="Invalid password.")

    session_id = str(uuid.uuid4())
    cursor.execute(
        """
        INSERT INTO session (session_id, user_id, created_at, expires_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + '1 year')
    """,
        (session_id, user["id"]),
    )

    db.commit()

    resp = make_response(render_template("session/login_success.html", state="success"))
    resp.set_cookie("session", session_id, max_age=datetime.timedelta(days=365))

    return resp


@bp.get("/setpassword")
def set_password():
    username = request.cookies.get("username") or ""
    username = username.strip()
    username = unicodedata.normalize("NFKC", username)
    if username:
        username = username.strip()
        username = unicodedata.normalize("NFKC", username)
    else:
        username = ""

    return render_template("session/set_password.html", username=username)


@bp.post("/setpassword")
def set_password_post():
    username = request.form.get("username", "")
    username = username.strip()
    username = unicodedata.normalize("NFKC", username)
    password = request.form.get("password", "")
    password2 = request.form.get("password2", "")

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template("session/set_password.html", message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template("session/set_password.html", message=password_message)
    if password != password2:
        return render_template("session/set_password.html", message="Passwords do not match.")

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, approved, password FROM account WHERE LOWER(username) = LOWER(%s)",
        (username,),
    )
    user = cursor.fetchone()
    if not user:
        return render_template("session/set_password.html", message="User not found.")
    if not user["approved"]:
        return render_template(
            "session/set_password.html",
            message="Your account is not approved yet. Please ping a moderator.",
        )
    if user["password"]:
        return render_template(
            "session/set_password.html",
            message=(
                "This account already has a password. "
                "Use the password reset form instead."
            ),
        )
    hashed, salt = hash_password(password)
    cursor.execute(
        """
        UPDATE account
        SET password = %s, salt = %s
        WHERE id = %s
    """,
        (hashed, salt, user["id"]),
    )

    db.commit()

    return render_template("session/set_password_success.html", state="success")


@bp.get("/signup")
def sign_up():
    username = request.cookies.get("username") or ""
    username = username.strip()
    username = unicodedata.normalize("NFKC", username)
    if username:
        username = username.strip()
        username = unicodedata.normalize("NFKC", username)
    else:
        username = ""

    return render_template("session/request_account.html", username=username)


@bp.post("/signup")
def sign_up_post():
    username = request.form.get("username", "")
    username = username.strip()
    username = unicodedata.normalize("NFKC", username)
    password = request.form.get("password", "")
    password2 = request.form.get("password2", "")
    email = request.form.get("email", "").strip()

    username_valid, username_message = validate_username(username)
    if not username_valid:
        return render_template("session/request_account.html", message=username_message)
    password_valid, password_message = validate_password(password)
    if not password_valid:
        return render_template("session/request_account.html", message=password_message)
    if password != password2:
        return render_template("session/request_account.html", message="Passwords do not match.")
    email_valid, email_message = validate_email(email)
    if not email_valid:
        return render_template(
            "session/request_account.html",
            message=email_message,
            username=username,
            email=email,
        )

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM account WHERE LOWER(username) = LOWER(%s)", (username,))
    user = cursor.fetchone()
    if user:
        return render_template(
            "session/request_account.html",
            message=(
                "Your account already exists as you have either voted or "
                "submitted entries before. Instead of signing up, please "
                "<a href='/setpassword'>set your password</a>."
            ),
        )

    hashed, salt = hash_password(password)
    cursor.execute(
        """
        INSERT INTO account (username, email, password, salt, approved)
        VALUES (%s, %s, %s, %s, false)
    """,
        (username, email or None, hashed, salt),
    )

    db.commit()

    return render_template("session/request_account_success.html", state="success")


@bp.get("/forgot-password")
def forgot_password():
    return render_template("session/forgot_password.html")


@bp.post("/forgot-password")
def forgot_password_post():
    email = request.form.get("email", "").strip()
    valid, message = validate_email(email, required=True)
    if not valid:
        return render_template("session/forgot_password.html", error=message), 400

    if is_configured():
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, username
            FROM account
            WHERE LOWER(email) = LOWER(%s) AND approved
            ORDER BY id
            LIMIT 1
            """,
            (email,),
        )
        user = cursor.fetchone()
        if user is not None:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).digest()
            cursor.execute(
                """
                UPDATE password_reset_token
                SET used_at = CURRENT_TIMESTAMP
                WHERE account_id = %s AND used_at IS NULL
                """,
                (user["id"],),
            )
            cursor.execute(
                """
                INSERT INTO password_reset_token (account_id, token_hash, expires_at)
                VALUES (
                    %s, %s,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                )
                """,
                (user["id"], token_hash, current_app.config["PASSWORD_RESET_MAX_AGE"]),
            )
            db.commit()
            reset_url = external_url(url_for("session.reset_password", token=token))
            try:
                send_email(
                    email,
                    "Reset your World Stage password",
                    f"Hello {user['username']},\n\n"
                    f"Reset your password using this link:\n{reset_url}\n\n"
                    "If you did not request this, you can ignore this email.\n",
                )
            except (OSError, RuntimeError, ValueError, smtplib.SMTPException):
                current_app.logger.exception("Could not send password reset email")

    return render_template("session/forgot_password_sent.html")


def _reset_account(token: str) -> dict | None:
    try:
        token_hash = hashlib.sha256(token.encode()).digest()
    except UnicodeError:
        return None
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT password_reset_token.id, password_reset_token.account_id,
               account.username
        FROM password_reset_token
        JOIN account ON account.id = password_reset_token.account_id
        WHERE password_reset_token.token_hash = %s
          AND password_reset_token.used_at IS NULL
          AND password_reset_token.expires_at > CURRENT_TIMESTAMP
          AND account.approved
        """,
        (token_hash,),
    )
    return cursor.fetchone()


@bp.get("/reset-password/<token>")
def reset_password(token: str):
    reset = _reset_account(token)
    if reset is None:
        return render_template("session/reset_password.html", invalid=True), 400
    return render_template("session/reset_password.html", username=reset["username"])


@bp.post("/reset-password/<token>")
def reset_password_post(token: str):
    reset = _reset_account(token)
    if reset is None:
        return render_template("session/reset_password.html", invalid=True), 400

    password = request.form.get("password", "")
    password2 = request.form.get("password2", "")
    valid, message = validate_password(password)
    if not valid:
        return render_template(
            "session/reset_password.html", username=reset["username"], error=message
        ), 400
    if password != password2:
        return render_template(
            "session/reset_password.html",
            username=reset["username"],
            error="Passwords do not match.",
        ), 400

    hashed, salt = hash_password(password)
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE account SET password = %s, salt = %s WHERE id = %s",
        (hashed, salt, reset["account_id"]),
    )
    cursor.execute(
        "UPDATE password_reset_token SET used_at = CURRENT_TIMESTAMP WHERE id = %s",
        (reset["id"],),
    )
    cursor.execute("DELETE FROM session WHERE user_id = %s", (reset["account_id"],))
    db.commit()
    return render_template("session/set_password_success.html", state="reset")


@bp.get("/logout")
def logout():
    return render_template("session/logout.html")


@bp.post("/logout")
def logout_post():
    session = request.cookies.get("session", "")
    if not session:
        return render_template("session/logout_success.html", state="not_logged_in")

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        DELETE FROM session WHERE session_id = %s
    """,
        (session,),
    )

    db.commit()

    resp = make_response(render_template("session/logout_success.html", state="logged_out"))
    resp.delete_cookie("session")
    return resp
