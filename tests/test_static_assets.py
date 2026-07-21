import json
import math
import re
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

COLOUR_NAMES = (
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "black",
)
CSS_VARIABLE_RE = re.compile(r"--([\w-]+):\s*([^;]+);")
ROOT_BLOCK_RE = re.compile(r":root\s*\{(.*?)\}", re.DOTALL)
OKLCH_RE = re.compile(r"oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+|none)\)")


def _oklch_relative_luminance(value: str) -> float:
    match = OKLCH_RE.fullmatch(value)
    assert match is not None
    lightness_text, chroma_text, hue_text = match.groups()
    lightness = float(lightness_text) / 100
    chroma = float(chroma_text)
    hue_radians = math.radians(0 if hue_text == "none" else float(hue_text))
    a = chroma * math.cos(hue_radians)
    b = chroma * math.sin(hue_radians)

    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l_linear, m_linear, s_linear = l_**3, m_**3, s_**3
    linear_srgb = (
        4.0767416621 * l_linear
        - 3.3077115913 * m_linear
        + 0.2309699292 * s_linear,
        -1.2684380046 * l_linear
        + 2.6097574011 * m_linear
        - 0.3413193965 * s_linear,
        -0.0041960863 * l_linear
        - 0.7034186147 * m_linear
        + 1.707614701 * s_linear,
    )
    red, green, blue = (max(0, min(1, component)) for component in linear_srgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(first: float, second: float) -> float:
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def test_formatted_background_colours_meet_wcag_contrast():
    stylesheet = (
        Path(__file__).parents[1] / "world_stage/static/css/index.css"
    ).read_text()
    root_blocks = ROOT_BLOCK_RE.findall(stylesheet)
    assert len(root_blocks) >= 2

    light_variables = dict(CSS_VARIABLE_RE.findall(root_blocks[0]))
    dark_variables = {
        **light_variables,
        **dict(CSS_VARIABLE_RE.findall(root_blocks[1])),
    }

    for colour in COLOUR_NAMES:
        class_match = re.search(
            rf"\.background-colour-{colour}\s*\{{(.*?)\}}",
            stylesheet,
            re.DOTALL,
        )
        assert class_match is not None
        assert f"color: var(--on-{colour});" in class_match.group(1)

    for theme, variables in (("light", light_variables), ("dark", dark_variables)):
        for colour in COLOUR_NAMES:
            background = _oklch_relative_luminance(variables[colour])
            black_ratio = _contrast_ratio(background, 0)
            white_ratio = _contrast_ratio(background, 1)

            foreground_match = re.fullmatch(
                r"var\(--(black|white)\)", variables[f"on-{colour}"]
            )
            assert foreground_match is not None
            foreground = 0 if foreground_match.group(1) == "black" else 1
            selected_ratio = _contrast_ratio(background, foreground)

            assert selected_ratio >= 4.5, f"{theme} {colour}: {selected_ratio:.2f}:1"
            assert selected_ratio >= max(black_ratio, white_ratio) - 1e-9


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


def test_flag_images_are_not_selectable_or_draggable(client):
    response = client.get(
        "/country/", headers={"Accept": "text/html"}, follow_redirects=True
    )

    assert response.status_code == 200
    assert b'class="flag flag-image"' in response.data
    assert b'draggable="false"' in response.data

    stylesheet = client.get("/static/css/index.css")
    assert stylesheet.status_code == 200
    assert b".flag-image" in stylesheet.data
    assert b"user-select: none" in stylesheet.data
    assert b"-webkit-user-drag: none" in stylesheet.data


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
