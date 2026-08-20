import os
import sqlite3
import string
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from hypothesis import given
from hypothesis import strategies as st

from scripts.build_flag_catalog import collect_flag_assets
from world_stage import _environment_boolean, _flag_url

BOOLEAN_CASES = [
    *((value, True) for value in ("1", "true", "yes", "on")),
    *((value, False) for value in ("0", "false", "no", "off")),
]


def _flag_app():
    catalog = sqlite3.connect(":memory:")
    catalog.executescript(
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
    catalog.executemany(
        "INSERT INTO flag_asset VALUES (?, ?, ?, ?, ?)",
        [
            ("AA/rect.svg", "AA", "", "rect", "regular"),
            ("AA/rect-small.svg", "AA", "", "rect", "small"),
            ("AA/alt/rect.svg", "AA", "alt", "rect", "regular"),
            ("XX/rect.svg", "XX", "", "rect", "regular"),
            ("XX/square.svg", "XX", "", "square", "regular"),
        ],
    )
    app = Flask(__name__)
    app.config.update(
        STATIC_URL_PREFIX="/static",
        STATIC_RELEASE="release",
        FLAG_CATALOG=catalog,
    )
    return app, catalog


@given(
    width=st.integers(),
    country=st.sampled_from(["AA", "aa", "missing", ""]),
    shape=st.sampled_from(["rect", "square", "unsupported"]),
    variant=st.sampled_from(["", "alt", "missing", None]),
)
def test_flag_resolution_uses_available_specific_assets_then_falls_back(
    width, country, shape, variant
):
    flag_app, catalog = _flag_app()
    normalized_country = country.upper() if country else "XX"
    normalized_shape = shape if shape in {"rect", "square"} else "rect"
    normalized_variant = variant if variant in {"alt", "missing"} else ""

    expected_path = None
    countries = (normalized_country, "XX") if normalized_country != "XX" else ("XX",)
    variants = (normalized_variant, "") if normalized_variant else ("",)
    sizes = ("small", "regular") if width <= 36 else ("regular",)
    for candidate_country in countries:
        for candidate_variant in variants:
            for candidate_size in sizes:
                row = catalog.execute(
                    """SELECT relative_path FROM flag_asset
                       WHERE country_code = ? AND variant = ? AND shape = ? AND size = ?""",
                    (
                        candidate_country,
                        candidate_variant,
                        normalized_shape,
                        candidate_size,
                    ),
                ).fetchone()
                if row is not None:
                    expected_path = row[0]
                    break
            if expected_path is not None:
                break
        if expected_path is not None:
            break

    try:
        if expected_path is None:
            with pytest.raises(LookupError):
                _flag_url(flag_app, country, width, shape, variant)
        else:
            assert _flag_url(flag_app, country, width, shape, variant) == (
                f"/static/release/flags/{expected_path}"
            )
    finally:
        catalog.close()


@given(
    country=st.text(alphabet=string.ascii_letters, min_size=2, max_size=4),
    variant=st.text(
        alphabet=string.ascii_letters + string.digits + "_-",
        max_size=12,
    ).filter(lambda value: value.lower() not in {"old", "wip"}),
    shape=st.sampled_from(["rect", "square"]),
    size=st.sampled_from(["regular", "small"]),
    separator=st.sampled_from(["-", "_"]),
    uppercase=st.booleans(),
)
def test_catalog_collection_classifies_canonical_assets(
    country, variant, shape, size, separator, uppercase
):
    with tempfile.TemporaryDirectory() as temporary_directory:
        flags_root = Path(temporary_directory) / "flags"
        directory = flags_root / country
        if variant:
            directory /= variant
        directory.mkdir(parents=True)
        suffix = f"{separator}small" if size == "small" else ""
        filename = f"{shape}{suffix}.svg"
        if uppercase:
            filename = filename.upper()
        relative_path = (directory / filename).relative_to(flags_root).as_posix()
        (directory / filename).write_text("asset")
        (directory / "notes.txt").write_text("not an asset")
        archived = directory / "old"
        archived.mkdir()
        (archived / "rect.svg").write_text("archived")

        rows, manifest = collect_flag_assets(flags_root)

    assert rows == [
        (
            relative_path,
            country.upper(),
            variant,
            shape,
            size,
        )
    ]
    assert manifest[country.upper()][variant][shape][size] == f"flags/{relative_path}"


@given(
    case=st.sampled_from(BOOLEAN_CASES),
    uppercase=st.booleans(),
    left_padding=st.text(alphabet=" \t", max_size=5),
    right_padding=st.text(alphabet=" \t", max_size=5),
)
def test_environment_boolean_normalizes_supported_values(
    case, uppercase, left_padding, right_padding
):
    value, expected = case
    value = value.upper() if uppercase else value
    with patch.dict(
        os.environ,
        {"PROPERTY_BOOLEAN": f"{left_padding}{value}{right_padding}"},
    ):
        assert _environment_boolean("PROPERTY_BOOLEAN") is expected
