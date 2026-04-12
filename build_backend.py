from __future__ import annotations

from pathlib import Path
import brotli # type: ignore
import flit_core.buildapi as flit_buildapi # type: ignore

# Adjust this if your import package directory is named differently.
PACKAGE_DIRS = [
    Path("world_stage"),
]

ROOT_NAMES = {"files", "static"}

# Compress only text-like assets. Extend as needed.
COMPRESSIBLE_SUFFIXES = {
    ".css",
    ".js",
    ".mjs",
    ".html",
    ".svg",
    ".txt",
    ".xml",
    ".json",
    ".map",
    ".ico",
    ".webmanifest"
}

def _should_compress(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix == ".br":
        return False
    if any(part.startswith(".") for part in path.parts):
        return False
    return path.suffix.lower() in COMPRESSIBLE_SUFFIXES

def _generate_brotli_assets() -> None:
    for package_dir in PACKAGE_DIRS:
        if not package_dir.exists():
            continue

        for root_name in ROOT_NAMES:
            root = package_dir / root_name
            if not root.exists():
                continue

            for path in root.rglob("*"):
                if not _should_compress(path):
                    continue

                out_path = path.with_name(path.name + ".br")

                if out_path.exists() and out_path.stat().st_mtime >= path.stat().st_mtime:
                    continue

                compressed = brotli.compress(path.read_bytes(), quality=11)
                out_path.write_bytes(compressed)

def get_requires_for_build_wheel(config_settings=None):
    return flit_buildapi.get_requires_for_build_wheel(config_settings)

def get_requires_for_build_sdist(config_settings=None):
    return flit_buildapi.get_requires_for_build_sdist(config_settings)

def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    # Metadata generation does not need the assets.
    return flit_buildapi.prepare_metadata_for_build_wheel(
        metadata_directory,
        config_settings,
    )

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _generate_brotli_assets()
    return flit_buildapi.build_wheel(
        wheel_directory,
        config_settings,
        metadata_directory,
    )

def build_sdist(sdist_directory, config_settings=None):
    _generate_brotli_assets()
    return flit_buildapi.build_sdist(
        sdist_directory,
        config_settings,
    )