#!/usr/bin/env python3
"""Build the read-only SQLite catalogue for a deployed flag release."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

CANONICAL_FLAG = re.compile(r"^(rect|square)([-_]small)?\.svg$", re.IGNORECASE)
ARCHIVE_DIRECTORIES = {"old", "wip"}

FlagRow = tuple[str, str, str, str, str]
FlagManifest = dict[str, dict[str, dict[str, dict[str, str]]]]


def classify(path: Path) -> tuple[str | None, str | None]:
    match = CANONICAL_FLAG.fullmatch(path.name)
    if match is None:
        return None, None
    return match.group(1).lower(), "small" if match.group(2) else "regular"


def collect_flag_assets(flags_root: Path) -> tuple[list[FlagRow], FlagManifest]:
    if not flags_root.is_dir():
        raise ValueError(f"Flag directory does not exist: {flags_root}")

    rows: list[FlagRow] = []
    manifest: FlagManifest = {}
    for path in sorted(flags_root.rglob("*")):
        if not path.is_file():
            continue

        relative_path = path.relative_to(flags_root)
        if len(relative_path.parts) < 2 or any(
            part.lower() in ARCHIVE_DIRECTORIES for part in relative_path.parts[1:-1]
        ):
            continue

        shape, size = classify(relative_path)
        if shape is None or size is None:
            continue

        relative_path_string = relative_path.as_posix()
        country_code = relative_path.parts[0].upper()
        variant = "/".join(relative_path.parts[1:-1])
        rows.append((relative_path_string, country_code, variant, shape, size))
        size_map = (
            manifest.setdefault(country_code, {})
            .setdefault(variant, {})
            .setdefault(shape, {})
        )
        if size in size_map:
            raise ValueError(
                f"Duplicate flag choice for {country_code}/{variant}/{shape}/{size}"
            )
        size_map[size] = f"flags/{relative_path_string}"

    return rows, manifest


def initialize_catalog(db: sqlite3.Connection, rows: list[FlagRow]) -> None:
    db.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;

        CREATE TABLE flag_asset (
            relative_path TEXT PRIMARY KEY,
            country_code TEXT NOT NULL,
            variant TEXT NOT NULL,
            shape TEXT NOT NULL,
            size TEXT NOT NULL
        ) STRICT;

        CREATE INDEX flag_asset_lookup
            ON flag_asset(country_code, variant, shape, size);
        """
    )
    db.executemany(
        """
        INSERT INTO flag_asset (
            relative_path, country_code, variant, shape, size
        ) VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.execute("PRAGMA user_version = 2")


def manifest_source(manifest: FlagManifest) -> str:
    manifest_json = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    return f"window.WORLD_STAGE_FLAGS={manifest_json};\n"


def build_catalog(flags_root: Path, database_path: Path, manifest_path: Path) -> None:
    rows, manifest = collect_flag_assets(flags_root)

    with sqlite3.connect(database_path) as db:
        initialize_catalog(db, rows)

    manifest_path.write_text(
        manifest_source(manifest),
        encoding="utf-8",
    )


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "Usage: build_flag_catalog.py FLAGS_DIR OUTPUT_DB OUTPUT_MANIFEST_JS"
        )
    build_catalog(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))


if __name__ == "__main__":
    main()
