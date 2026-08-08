import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from urllib.parse import urljoin

from flask import current_app


def validate_email(email: str, *, required: bool = False) -> tuple[bool, str]:
    if not email:
        return (False, "Email is required.") if required else (True, "")
    parsed = parseaddr(email)[1]
    if parsed != email or len(email) > 254 or "@" not in email or email.startswith("@"):
        return False, "Enter a valid email address."
    local_part, domain = email.rsplit("@", 1)
    if not local_part or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return False, "Enter a valid email address."
    return True, ""


def is_configured() -> bool:
    return bool(
        current_app.config.get("MAIL_SERVER", "").strip()
        and current_app.config.get("MAIL_DEFAULT_SENDER", "").strip()
        and current_app.config.get("SITE_URL", "").strip()
    )


def external_url(path: str) -> str:
    """Build a public URL without trusting the request's Host header."""
    base_url = current_app.config.get("SITE_URL", "").strip()
    if not base_url:
        raise RuntimeError("SITE_URL is required for email links")
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def send_email(recipient: str, subject: str, body: str) -> None:
    """Send one plain-text email using the application's SMTP configuration."""
    sender = current_app.config.get("MAIL_DEFAULT_SENDER", "").strip()
    server = current_app.config.get("MAIL_SERVER", "").strip()
    if not sender or not server:
        raise RuntimeError("MAIL_DEFAULT_SENDER and MAIL_SERVER are required to send email")

    message = EmailMessage()
    sender_name = current_app.config.get("MAIL_SENDER_NAME", "").strip()
    message["From"] = formataddr((sender_name, sender)) if sender_name else sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    if current_app.config.get("MAIL_SUPPRESS_SEND", False):
        current_app.extensions.setdefault("mail_outbox", []).append(message)
        return

    port = current_app.config["MAIL_PORT"]
    timeout = current_app.config["MAIL_TIMEOUT"]
    smtp_class = smtplib.SMTP_SSL if current_app.config["MAIL_USE_SSL"] else smtplib.SMTP
    with smtp_class(server, port, timeout=timeout) as smtp:
        if current_app.config["MAIL_USE_TLS"]:
            smtp.starttls()
        username = current_app.config.get("MAIL_USERNAME", "")
        password = current_app.config.get("MAIL_PASSWORD", "")
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def init_app(app) -> None:
    if app.config["MAIL_USE_TLS"] and app.config["MAIL_USE_SSL"]:
        raise ValueError("MAIL_USE_TLS and MAIL_USE_SSL cannot both be enabled")
    if app.config["PASSWORD_RESET_MAX_AGE"] <= 0:
        raise ValueError("PASSWORD_RESET_MAX_AGE must be positive")
    app.extensions.setdefault("mail_outbox", [])
