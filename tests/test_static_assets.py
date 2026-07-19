import json
import sqlite3
from pathlib import Path

from flask import Flask

from scripts.build_flag_catalog import build_catalog
from world_stage import (
    _configure_local_assets,
    _current_static_release,
    _environment_boolean,
    _flag_url,
    _static_url,
)


def test_current_static_release_uses_deployment_symlink(tmp_path: Path):
    static_root = tmp_path / "static"
    release = static_root / "assets" / "release-20260719"
    release.mkdir(parents=True)
    (static_root / "current").symlink_to("assets/release-20260719")

    assert _current_static_release(str(static_root)) == "release-20260719"


def test_static_url_includes_current_release():
    app = Flask(__name__)
    app.config.update(STATIC_URL_PREFIX="/static", STATIC_RELEASE="release-20260719")

    assert _static_url(app, "images/bias.png") == "/static/release-20260719/images/bias.png"


def test_static_url_uses_unversioned_path_without_a_deployed_release():
    app = Flask(__name__)
    app.config.update(STATIC_URL_PREFIX="/static", STATIC_RELEASE=None)

    assert _static_url(app, "css/index.css") == "/static/css/index.css"


def test_flag_url_selects_small_assets_and_falls_back_to_regular(tmp_path: Path):
    catalogue_path = tmp_path / "flags.sqlite"
    with sqlite3.connect(catalogue_path) as catalogue:
        catalogue.executescript(
            """
            CREATE TABLE flag_asset (
                relative_path TEXT PRIMARY KEY,
                country_code TEXT NOT NULL,
                variant TEXT NOT NULL,
                shape TEXT NOT NULL,
                size TEXT NOT NULL
            );
            """
        )
        catalogue.executemany(
            """
            INSERT INTO flag_asset VALUES (?, ?, '', ?, ?)
            """,
            [
                ("AA/rect.svg", "AA", "rect", "regular"),
                ("AA/rect-small.svg", "AA", "rect", "small"),
                ("XX/rect.svg", "XX", "rect", "regular"),
                ("XX/square.svg", "XX", "square", "regular"),
            ],
        )

    app = Flask(__name__)
    app.config.update(STATIC_URL_PREFIX="/static", STATIC_RELEASE="release-20260719")
    app.config["FLAG_CATALOG"] = sqlite3.connect(
        f"{catalogue_path.as_uri()}?mode=ro", uri=True
    )

    assert _flag_url(app, "aa", 30) == "/static/release-20260719/flags/AA/rect-small.svg"
    assert _flag_url(app, "aa", 80) == "/static/release-20260719/flags/AA/rect.svg"
    assert _flag_url(app, "aa", 80, variant=None) == "/static/release-20260719/flags/AA/rect.svg"
    assert _flag_url(app, "missing", 80) == "/static/release-20260719/flags/XX/rect.svg"

    assert (
        _flag_url(app, "aa", 30, "square")
        == "/static/release-20260719/flags/XX/square.svg"
    )


def test_flag_url_requires_a_catalogue():
    app = Flask(__name__)
    app.config.update(STATIC_URL_PREFIX="/static", STATIC_RELEASE=None)
    app.extensions["flag_catalog"] = {"pid": None, "connection": None}

    try:
        _flag_url(app, "AA", 30)
    except RuntimeError as error:
        assert str(error) == "Flag catalogue is not available"
    else:
        raise AssertionError("Flag resolution unexpectedly worked without a catalogue")


def test_flag_catalog_builder_writes_javascript_manifest(tmp_path: Path):
    flags_root = tmp_path / "flags"
    (flags_root / "AA").mkdir(parents=True)
    (flags_root / "AA" / "old").mkdir()
    (flags_root / "AA" / "wip").mkdir()
    (flags_root / "AA" / "rect.svg").write_text("<svg/>")
    (flags_root / "AA" / "rect-small.svg").write_text("<svg/>")
    (flags_root / "AA" / "notes.txt").write_text("not a flag")
    (flags_root / "AA" / "old" / "square.svg").write_text("<svg/>")
    (flags_root / "AA" / "wip" / "rect.svg").write_text("<svg/>")

    database_path = tmp_path / "flags.sqlite"
    manifest_path = tmp_path / "flag-manifest.js"
    build_catalog(flags_root, database_path, manifest_path)

    manifest_source = manifest_path.read_text()
    prefix = "window.WORLD_STAGE_FLAGS="
    assert manifest_source.startswith(prefix)
    manifest = json.loads(manifest_source.removeprefix(prefix).removesuffix(";\n"))
    assert manifest["AA"][""]["rect"] == {
        "regular": "flags/AA/rect.svg",
        "small": "flags/AA/rect-small.svg",
    }
    assert set(manifest["AA"]) == {""}

    with sqlite3.connect(database_path) as catalogue:
        columns = [row[1] for row in catalogue.execute("PRAGMA table_info(flag_asset)")]
        paths = [row[0] for row in catalogue.execute("SELECT relative_path FROM flag_asset")]
        schema_version = catalogue.execute("PRAGMA user_version").fetchone()[0]
    assert columns == ["relative_path", "country_code", "variant", "shape", "size"]
    assert paths == ["AA/rect-small.svg", "AA/rect.svg"]
    assert schema_version == 2


def test_local_assets_are_served_without_generated_files():
    app = Flask("world_stage", static_folder=None)
    app.config.update(STATIC_URL_PREFIX="/static", STATIC_RELEASE=None)
    app.extensions["flag_catalog"] = {"pid": None, "connection": None}
    _configure_local_assets(app)

    client = app.test_client()
    assert client.get("/static/css/index.css").status_code == 200
    assert client.get("/static/flags/XX/square.svg").status_code == 200
    assert client.get("/static/flag-manifest.js").status_code == 200
    assert client.get("/robots.txt").status_code == 200
    assert client.get("/favicon.ico").status_code == 200
    assert _flag_url(app, "missing", 30, "square") == "/static/flags/XX/square.svg"


def test_local_assets_environment_variable(monkeypatch):
    monkeypatch.setenv("LOCAL_ASSETS", "yes")
    assert _environment_boolean("LOCAL_ASSETS") is True

    monkeypatch.setenv("LOCAL_ASSETS", "off")
    assert _environment_boolean("LOCAL_ASSETS") is False
