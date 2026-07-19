from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import flit_core.buildapi as flit_buildapi  # type: ignore
from flit_core.common import Module  # type: ignore

PUBLIC_ASSET_DIRECTORIES = {"files", "static"}


@contextmanager
def _exclude_public_assets_from_package():
    """Keep deploy-only assets out of wheels.

    Flit treats every file below a package as package data and does not expose
    a wheel-level exclusion setting. Patch its file iterator only while a
    build runs; application source remains untouched on disk.
    """

    original_iter_files = Module.iter_files

    def iter_files_without_public_assets(module):
        for filename in original_iter_files(module):
            relative_path = Path(filename).relative_to(module.path)
            if relative_path.parts[0] not in PUBLIC_ASSET_DIRECTORIES:
                yield filename

    Module.iter_files = iter_files_without_public_assets
    try:
        yield
    finally:
        Module.iter_files = original_iter_files


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
    with _exclude_public_assets_from_package():
        return flit_buildapi.build_wheel(
            wheel_directory,
            config_settings,
            metadata_directory,
        )


def build_sdist(sdist_directory, config_settings=None):
    return flit_buildapi.build_sdist(
        sdist_directory,
        config_settings,
    )


def get_requires_for_build_editable(config_settings=None):
    return flit_buildapi.get_requires_for_build_editable(config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return flit_buildapi.prepare_metadata_for_build_editable(
        metadata_directory,
        config_settings,
    )


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return flit_buildapi.build_editable(
        wheel_directory,
        config_settings,
        metadata_directory,
    )
