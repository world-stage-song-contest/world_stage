import contextlib
import os
import re
import sqlite3
import urllib.parse
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, Response, send_from_directory
from markupsafe import Markup, escape
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from werkzeug.middleware.proxy_fix import ProxyFix

from .logging_setup import configure_logging

URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.I)

TRAILING_PUNCT = ".,;:!?"

BRACKET_PAIRS = {
    ")": "(",
    "]": "[",
    "}": "{",
}

STATIC_RELEASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
FLAG_VARIANT_RE = re.compile(r"[A-Za-z0-9_-]+")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _environment_boolean(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off")


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

        trimmed_tail = raw[len(url) :]

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


def _current_static_release(static_root: str) -> str | None:
    """Read the release selected by the deployer's ``current`` symlink."""
    current = Path(static_root) / "current"
    try:
        release_path = current.resolve(strict=True)
    except OSError:
        return None

    if release_path.parent.name != "assets":
        return None

    release = release_path.name
    return release if STATIC_RELEASE_RE.fullmatch(release) else None


def _static_url(app: Flask, filename: str) -> str:
    relative_path = filename.lstrip("/")
    if not relative_path or any(part in {"", ".", ".."} for part in relative_path.split("/")):
        raise ValueError(f"Invalid static asset path: {filename!r}")

    prefix = app.config["STATIC_URL_PREFIX"].rstrip("/")
    release = app.config.get("STATIC_RELEASE")
    if release:
        return f"{prefix}/{release}/{relative_path}"
    return f"{prefix}/{relative_path}"


def _static_root_url(app: Flask) -> str:
    prefix = app.config["STATIC_URL_PREFIX"].rstrip("/")
    release = app.config.get("STATIC_RELEASE")
    return f"{prefix}/{release}" if release else prefix


def _open_flag_catalog(path: str | None) -> sqlite3.Connection | None:
    if path is None or not os.path.isfile(path):
        return None

    uri = f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def _flag_catalog(app: Flask) -> sqlite3.Connection | None:
    """Open one immutable catalogue connection in each Gunicorn worker."""
    configured_catalog = app.config.get("FLAG_CATALOG")
    if configured_catalog is not None:
        return configured_catalog

    state = app.extensions["flag_catalog"]
    worker_pid = os.getpid()
    if state["pid"] != worker_pid:
        connection = state["connection"]
        if connection is not None:
            connection.close()
        state["pid"] = worker_pid
        state["connection"] = _open_flag_catalog(app.config.get("FLAG_CATALOG_PATH"))
    return state["connection"]


def _catalog_flag_path(
    catalog: sqlite3.Connection,
    country: str,
    variant: str,
    shape: str,
    size: str,
) -> str | None:
    sizes = ("small", "regular") if size == "small" else ("regular",)
    placeholders = ", ".join("?" for _ in sizes)
    row = catalog.execute(
        f"""
        SELECT relative_path
        FROM flag_asset
        WHERE country_code = ?
          AND variant = ?
          AND shape = ?
          AND size IN ({placeholders})
        ORDER BY CASE size WHEN ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (country, variant, shape, *sizes, size),
    ).fetchone()
    return row[0] if row else None


def _flag_url(
    app: Flask,
    country: str,
    width: int,
    shape: str = "rect",
    variant: str | None = "",
) -> str:
    country = (country or "XX").upper()
    shape = shape if shape in {"rect", "square"} else "rect"
    variant = variant if variant and FLAG_VARIANT_RE.fullmatch(variant) else ""
    size = "small" if width <= 40 else "regular"
    catalog = _flag_catalog(app)

    if catalog is None:
        raise RuntimeError("Flag catalogue is not available")

    country_codes = (country, "XX") if country != "XX" else ("XX",)
    variants = (variant, "") if variant else ("",)
    for country_code in country_codes:
        for candidate_variant in variants:
            path = _catalog_flag_path(
                catalog, country_code, candidate_variant, shape, size
            )
            if path is not None:
                return _static_url(app, f"flags/{path}")

    raise LookupError(f"No {shape} flag asset exists for {country} or XX")


def _configure_local_assets(app: Flask) -> None:
    """Serve source assets and keep an in-memory flag catalogue for development."""
    from scripts.build_flag_catalog import (
        collect_flag_assets,
        initialize_catalog,
        manifest_source,
    )

    static_root = Path(app.root_path) / "static"
    files_root = Path(app.root_path) / "files"
    flags_root = files_root / "flags"
    favicons_root = files_root / "favicons"
    rows, manifest = collect_flag_assets(flags_root)

    catalog = sqlite3.connect(":memory:", check_same_thread=False)
    initialize_catalog(catalog, rows)
    app.config["FLAG_CATALOG"] = catalog
    local_manifest_source = manifest_source(manifest)

    @app.get("/static/<path:filename>")
    def local_static_asset(filename: str):
        if filename == "flag-manifest.js":
            return Response(local_manifest_source, content_type="application/javascript")
        if filename.startswith("flags/"):
            return send_from_directory(flags_root, filename.removeprefix("flags/"))
        return send_from_directory(static_root, filename)

    @app.get("/robots.txt")
    def local_robots():
        return send_from_directory(files_root, "robots.txt")

    @app.get("/favicon.ico")
    def local_favicon():
        return send_from_directory(favicons_root, "favicon.ico")

    @app.get("/favicons/<path:filename>")
    def local_favicon_asset(filename: str):
        return send_from_directory(favicons_root, filename)


def create_app(config: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__, instance_relative_config=True, static_folder=None)
    app.config.from_mapping(
        SECRET_KEY="dev",
        DATABASE=os.path.join(app.instance_path, "songs.db"),
        LOCAL_ASSETS=_environment_boolean("LOCAL_ASSETS"),
        PERFORMANCE_HEADERS=_environment_boolean("PERFORMANCE_HEADERS"),
        STATIC_ROOT="/opt/worldstage/static",
        STATIC_URL_PREFIX="/static",
    )

    if config is not None:
        app.config.update(config)

    app.config.from_envvar("WORLD_STAGE_CONFIG", silent=True)

    app.config.from_pyfile("config.py", silent=True)

    if app.config["LOCAL_ASSETS"]:
        app.config["STATIC_RELEASE"] = None
        app.config["FLAG_CATALOG_PATH"] = None
    else:
        configured_release = app.config.get("STATIC_RELEASE")
        if configured_release:
            if not STATIC_RELEASE_RE.fullmatch(configured_release):
                raise ValueError("STATIC_RELEASE must be a valid public asset release name")
        else:
            app.config["STATIC_RELEASE"] = _current_static_release(
                app.config["STATIC_ROOT"]
            )

    if app.config.get("STATIC_RELEASE") and not app.config.get("FLAG_CATALOG_PATH"):
        app.config["FLAG_CATALOG_PATH"] = str(
            Path(app.config["STATIC_ROOT"])
            / "catalogues"
            / f"{app.config['STATIC_RELEASE']}.sqlite"
        )
    # Gunicorn loads the application before it forks workers. Delay opening
    # SQLite until the first flag render in each worker so no connection is
    # inherited across forks.
    app.config.setdefault("FLAG_CATALOG", None)
    app.extensions["flag_catalog"] = {"pid": None, "connection": None}
    if app.config["LOCAL_ASSETS"]:
        _configure_local_assets(app)

    from .db import InstrumentedCursor

    app.config["DB_POOL"] = ConnectionPool(
        conninfo=app.config.get("DATABASE_URI", os.environ.get("DATABASE_URI", "")),
        min_size=1,
        max_size=10,
        timeout=10.0,
        kwargs={"row_factory": dict_row, "cursor_factory": InstrumentedCursor},
    )

    app.url_map.strict_slashes = False
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    app.jinja_env.globals.update(zip=zip)
    app.jinja_env.globals.update(round=round)
    app.jinja_env.globals.update(int=int)
    app.jinja_env.globals.update(static_url=lambda filename: _static_url(app, filename))
    app.jinja_env.globals.update(static_root_url=lambda: _static_root_url(app))
    app.jinja_env.globals.update(
        flag_url=lambda country, width, shape="rect", variant="": _flag_url(
            app, country, width, shape, variant
        )
    )

    app.jinja_env.filters.update(urldecode=urllib.parse.unquote)
    app.jinja_env.filters.update(urlize_decoded=urlize_decoded)

    from . import performance

    performance.init_app(app)

    @app.before_request
    def clear_trailing():
        from flask import redirect, request

        rp = request.path
        if rp != "/" and rp.endswith("/"):
            return redirect(rp[:-1])

    with contextlib.suppress(OSError):
        os.makedirs(app.instance_path)

    from . import db, media, scrobble

    db.init_app(app)
    media.init_app(app)
    scrobble.init_app(app)

    from .routes import (
        admin,
        api,
        country,
        index,
        member,
        playlist,
        radio,
        results,
        revote,
        session,
        user,
        vote,
        year,
    )

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
    app.register_blueprint(playlist.bp)
    app.register_blueprint(radio.bp)
    app.register_blueprint(revote.bp)

    return app
