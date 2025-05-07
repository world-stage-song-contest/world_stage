import os
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE=os.path.join(app.instance_path, 'songs.db'),
    )

    app.config.from_envvar('WORLD_STAGE_CONFIG', silent=True)

    app.config.from_pyfile('config.py', silent=True)
    app.url_map.strict_slashes = False
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

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

    from . import index
    from . import vote
    from . import results
    app.register_blueprint(index.bp)
    app.register_blueprint(vote.bp)
    app.register_blueprint(results.bp)

    return app