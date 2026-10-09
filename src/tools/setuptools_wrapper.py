import subprocess
import sys

from setuptools import build_meta as _orig
from setuptools.build_meta import *  # noqa: F403

# The wildcard import re-exports the remaining PEP 517/660 hooks.


def compile_ui() -> None:
    subprocess.run(
        [sys.executable, "-m", "src.tools.ui_compile"], check=True
    )


def get_requires_for_build_wheel(config_settings=None):
    return [
        *_orig.get_requires_for_build_wheel(config_settings), "pyside6~=6.8"
    ]


def get_requires_for_build_sdist(config_settings=None):
    return [
        *_orig.get_requires_for_build_sdist(config_settings), "pyside6~=6.8"
    ]


def get_requires_for_build_editable(config_settings=None):
    return get_requires_for_build_wheel(config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    compile_ui()
    return _orig.build_wheel(
        wheel_directory, config_settings, metadata_directory
    )


def build_sdist(sdist_directory, config_settings=None):
    compile_ui()
    return _orig.build_sdist(sdist_directory, config_settings)


def build_editable(
    wheel_directory, config_settings=None, metadata_directory=None
):
    compile_ui()
    return _orig.build_editable(
        wheel_directory, config_settings, metadata_directory
    )
