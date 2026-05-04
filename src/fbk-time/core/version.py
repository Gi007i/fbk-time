"""Application version.

Reads the version string from pyproject.toml at import time so the value
is available everywhere without re-parsing the manifest on each access.
"""

import tomllib
from pathlib import Path


def _read_version() -> str:
    """Read project version from pyproject.toml."""
    pyproject_path = Path(__file__).resolve().parent.parent / 'pyproject.toml'
    with open(pyproject_path, 'rb') as f:
        data = tomllib.load(f)
    return data['project']['version']


APP_VERSION = _read_version()
