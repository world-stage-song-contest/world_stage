import os
import re
from flask import Flask
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from werkzeug.middleware.proxy_fix import ProxyFix
import urllib.parse

from urllib.parse import unquote
from markupsafe import Markup, escape

URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.I)

TRAILING_PUNCT = ".,;:!?"

BRACKET_PAIRS = {
    ")": "(",
    "]": "[",
    "}": "{",
}

def _trim_trailing(url: str) -> str:
    url = url.rstrip(TRAILING_PUNCT)
    changed = True
    while changed and url:
        changed = False
        last = url[-1]
        if last in BRACKET_PAIRS:
            opener = BRACKET_PAIRS[last]
            if url.count(last) > url.count(opener):
                url = url[:-1]
                url = url.rstrip(TRAILING_PUNCT)
                changed = True
    return url

def urlize_decoded(value):
    if not value:
        return ""

    parts = []
    last = 0

    for m in URL_RE.finditer(value):
        start, end = m.span()
        raw = m.group(0)
        url = _trim_trailing(raw)

        trimmed_tail = raw[len(url):]

        parts.append(escape(value[last:start]))
        parts.append(
            Markup('<a href="{href}" rel="noopener">{text}</a>').format(
                href=escape(url),
                text=escape(unquote(url)),
            )
        )
        if trimmed_tail:
            parts.append(escape(trimmed_tail))

        last = end

    parts.append(escape(value[last:]))
    return Markup("").join(parts)

def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE=os.path.join(app.instance_path, 'songs.db'),
    )

    if config is not None:
        app.config.update(config)

    app.config.from_envvar('WORLD_STAGE_CONFIG', silent=True)

    app.config.from_pyfile('config.py', silent=True)

    app.config["DB_POOL"] = ConnectionPool(
        conninfo=app.config.get("DATABASE_URI", os.environ.get('DATABASE_URI', '')),
        min_size=1,
        max_size=10,
        timeout=10.0,
        kwargs={"row_factory": dict_row},
    )

    app.url_map.strict_slashes = False
    app.wsgi_app = ProxyFix( # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    app.jinja_env.globals.update(zip=zip)
    app.jinja_env.globals.update(round=round)
    app.jinja_env.globals.update(int=int)

    app.jinja_env.filters.update(urldecode=urllib.parse.unquote)
    app.jinja_env.filters.update(urlize_decoded=urlize_decoded)

    @app.before_request
    def clear_trailing():
        from flask import redirect, request

        rp = request.path
        if rp != '/' and rp.endswith('/'):
            return redirect(rp[:-1])

    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    from . import db
    db.init_app(app)

    from .routes import index
    from .routes import vote
    from .routes import results
    from .routes import session
    from .routes import member
    from .routes import year
    from .routes import user
    from .routes import admin
    from .routes import country
    from .routes import api

    app.register_blueprint(index.bp)
    app.register_blueprint(vote.bp)
    app.register_blueprint(results.bp)
    app.register_blueprint(session.bp)
    app.register_blueprint(member.bp)
    app.register_blueprint(year.bp)
    app.register_blueprint(user.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(country.bp)
    app.register_blueprint(api.bp)

    return app