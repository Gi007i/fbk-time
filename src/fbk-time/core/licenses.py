"""License management.

Generates and caches dependency license information using pip-licenses.
Uses smart caching: only regenerates when requirements.txt changes.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Allowed URL schemes (blocks javascript:, data:, etc.)
ALLOWED_URL_SCHEMES = ('http://', 'https://')


def _sanitize_url(url: str | None) -> str | None:
    """Validate URL scheme to prevent XSS via javascript: or data: URIs.

    Args:
        url: URL string to validate.

    Returns:
        Original URL if safe, None otherwise.
    """
    if not url or url == 'UNKNOWN':
        return None
    if url.lower().startswith(ALLOWED_URL_SCHEMES):
        return url
    return None


def get_licenses_path() -> Path:
    """Return path to the licenses.json file from config."""
    from config import Config
    return Config.LICENSES_PATH


def get_manual_licenses_path() -> Path:
    """Return path to the manual-licenses.json file from config."""
    from config import Config
    return Config.MANUAL_LICENSES_PATH


def get_requirements_path() -> Path:
    """Return path to requirements.txt."""
    return Path(__file__).parent.parent / 'requirements.txt'


def needs_regeneration() -> bool:
    """Check if licenses.json needs to be regenerated.

    Returns True if:
    - licenses.json does not exist
    - requirements.txt is newer than licenses.json
    """
    licenses_path = get_licenses_path()
    requirements_path = get_requirements_path()

    if not licenses_path.exists():
        return True

    if not requirements_path.exists():
        return False

    return requirements_path.stat().st_mtime > licenses_path.stat().st_mtime


def generate_licenses() -> bool:
    """Generate licenses.json from installed packages using pip-licenses.

    Returns:
        True if generation was successful, False otherwise.
    """
    licenses_path = get_licenses_path()
    licenses_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                sys.executable,
                '-m', 'piplicenses',
                '--format=json',
                '--with-urls',
                '--with-authors',
                '--with-description',
                '--with-license-file',
                '--no-license-path'
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            if 'No module named' in result.stderr:
                logger.warning("pip-licenses not installed, skipping license generation")
            else:
                logger.error("pip-licenses failed: %s", result.stderr)
            return False

        licenses_data = json.loads(result.stdout)

        for pkg in licenses_data:
            pkg['URL'] = _sanitize_url(pkg.get('URL'))
            pkg.pop('Version', None)

        licenses_data.sort(key=lambda x: x.get('Name', '').lower())

        # Write then rename so concurrent Gunicorn workers regenerating at
        # startup never observe a half-written file.
        tmp_path = Path(f'{licenses_path}.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(licenses_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, licenses_path)

        logger.info("Generated licenses.json with %d packages", len(licenses_data))
        return True

    except subprocess.TimeoutExpired:
        logger.error("pip-licenses timed out")
        return False
    except FileNotFoundError:
        logger.warning("Python interpreter not found, skipping license generation")
        return False
    except json.JSONDecodeError as e:
        logger.error("Failed to parse pip-licenses output: %s", e)
        return False
    except OSError as e:
        logger.error("Failed to write licenses.json: %s", e)
        return False


def ensure_licenses_current() -> None:
    """Ensure licenses.json is up-to-date.

    Called on app startup. Only regenerates if requirements.txt has changed.
    """
    if needs_regeneration():
        logger.info("Regenerating licenses.json (requirements changed)")
        generate_licenses()
    else:
        logger.debug("licenses.json is current, skipping regeneration")


def _load_json_file(path: Path) -> list[dict]:
    """Load and parse a JSON license file."""
    if not path.exists():
        return []

    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load %s: %s", path.name, e)
        return []


def load_manual_licenses() -> list[dict]:
    """Load manually defined licenses from manual-licenses.json.

    Returns:
        List of license dictionaries, or empty list if file not found.
    """
    manual_path = get_manual_licenses_path()
    licenses = _load_json_file(manual_path)

    for pkg in licenses:
        pkg['URL'] = _sanitize_url(pkg.get('URL'))

    return licenses


def load_licenses() -> list[dict]:
    """Load all licenses from auto-generated and manual JSON files.

    Merges pip-licenses output with manually defined frontend/other licenses.
    Returns combined list sorted alphabetically by package name.

    Returns:
        List of license dictionaries, or empty list if no files found.
    """
    auto_licenses = _load_json_file(get_licenses_path())
    manual_licenses = load_manual_licenses()

    all_licenses = auto_licenses + manual_licenses
    all_licenses.sort(key=lambda x: x.get('Name', '').lower())

    return all_licenses


def get_license_summary() -> dict:
    """Get summary statistics about licenses.

    Returns:
        Dictionary with license type counts and total package count.
    """
    licenses = load_licenses()

    license_types = {}
    for pkg in licenses:
        license_name = pkg.get('License', 'Unknown')
        license_types[license_name] = license_types.get(license_name, 0) + 1

    return {
        'total': len(licenses),
        'by_type': dict(sorted(license_types.items(), key=lambda x: -x[1]))
    }
