import os
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
import urllib.parse

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
    app.url_map.strict_slashes = False
    app.wsgi_app = ProxyFix( # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    app.jinja_env.globals.update(zip=zip)
    app.jinja_env.globals.update(round=round)
    app.jinja_env.globals.update(int=int)

    app.jinja_env.filters.update(urldecode=urllib.parse.unquote)

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

    app.register_blueprint(index.bp)
    app.register_blueprint(vote.bp)
    app.register_blueprint(results.bp)
    app.register_blueprint(session.bp)
    app.register_blueprint(member.bp)
    app.register_blueprint(year.bp)
    app.register_blueprint(user.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(country.bp)

    return app