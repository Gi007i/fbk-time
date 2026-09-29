"""License management.

Generates and caches dependency license information from the metadata of
installed distributions. Uses smart caching: only regenerates when
requirements.txt changes.
"""

import json
import logging
import os
import re
import tempfile
from email.message import Message
from email.utils import getaddresses
from importlib import metadata as importlib_metadata
from pathlib import Path

logger = logging.getLogger(__name__)

# Blocks javascript:/data: URIs.
ALLOWED_URL_SCHEMES = ('http://', 'https://')

# Environment tooling, not application dependencies.
EXCLUDED_DISTRIBUTIONS = frozenset({'pip', 'setuptools', 'wheel'})

LICENSE_CLASSIFIER_PREFIX = 'License :: '
OSI_CLASSIFIER_PREFIX = 'License :: OSI Approved :: '
LICENSE_FILE_PATTERN = re.compile(r'^(LICEN[CS]E|COPYING)', re.IGNORECASE)
# License texts are small; anything bigger is not one.
LICENSE_FILE_MAX_BYTES = 1_000_000
# Checked in order; Home-page is absent on PEP 621 projects.
PROJECT_URL_LABELS = (
    'homepage', 'home-page', 'home page',
    'source', 'source code', 'repository', 'code', 'github',
)


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


def _canonical_name(name: str) -> str:
    """Normalize a distribution name per PEP 503.

    Duplicate installs of the same package (e.g. system package plus
    virtualenv copy) may spell the name differently; canonical names make
    them compare equal.

    Args:
        name: Distribution name as declared in its metadata.

    Returns:
        Canonical distribution name.
    """
    return re.sub(r'[-_.]+', '-', name).lower()


def _is_vendored(dist: importlib_metadata.Distribution) -> bool:
    """Check whether a distribution is vendored inside another package.

    setuptools >= 71 puts its vendored dist-infos on sys.path once imported;
    they would otherwise be listed as installed packages (or duplicates of
    real ones).

    Args:
        dist: Installed distribution.

    Returns:
        True if the distribution lives directly in a _vendor directory.
    """
    return Path(str(dist.locate_file(''))).name == '_vendor'


def _license_name(meta: Message) -> str:
    """Resolve the license name of a distribution.

    Checks the PEP 639 license expression, then trove classifiers, then the
    legacy License field.

    Args:
        meta: Distribution metadata.

    Returns:
        License name, or 'Unknown' if the metadata declares none.
    """
    expression = meta.get('License-Expression')
    if expression:
        return expression

    classifiers = [
        c.removeprefix(OSI_CLASSIFIER_PREFIX).removeprefix(LICENSE_CLASSIFIER_PREFIX)
        for c in meta.get_all('Classifier', [])
        if c.startswith(LICENSE_CLASSIFIER_PREFIX)
    ]
    classifiers = [c for c in classifiers if c != 'OSI Approved']
    if classifiers:
        return '; '.join(classifiers)

    license_field = (meta.get('License') or '').strip()
    if license_field and license_field != 'UNKNOWN':
        # Some distributions put the full license text into this field;
        # only its first line qualifies as a name.
        return license_field.splitlines()[0].strip()
    return 'Unknown'


def _author(meta: Message) -> str | None:
    """Resolve the author from the Author field or Author-email names.

    Args:
        meta: Distribution metadata.

    Returns:
        Author name(s), or None if the metadata declares none.
    """
    author = (meta.get('Author') or '').strip()
    if author and author != 'UNKNOWN':
        return author

    names = [name for name, _ in getaddresses([meta.get('Author-email') or '']) if name]
    return ', '.join(names) or None


def _homepage_url(meta: Message) -> str | None:
    """Resolve the project homepage from Home-page or Project-URL entries.

    Args:
        meta: Distribution metadata.

    Returns:
        Homepage URL, or None if the metadata declares none.
    """
    url = (meta.get('Home-page') or '').strip()
    if url and url != 'UNKNOWN':
        return url

    project_urls = {}
    for entry in meta.get_all('Project-URL', []):
        label, _, target = entry.partition(',')
        project_urls.setdefault(label.strip().lower(), target.strip())

    for label in PROJECT_URL_LABELS:
        if project_urls.get(label):
            return project_urls[label]
    return None


def _read_license_file(dist: importlib_metadata.Distribution, file) -> str | None:
    """Read one bundled license file with containment and size checks.

    File paths originate from the package's own RECORD and License-File
    entries; refuse paths or symlinks escaping the distribution as well as
    oversized files (defense in depth, the text is rendered in the application).

    Args:
        dist: Installed distribution.
        file: Package path of the license file.

    Returns:
        File content, or None if the file is refused or unreadable.
    """
    name = dist.metadata.get('Name')
    root = Path(str(dist.locate_file(''))).resolve()
    try:
        resolved = Path(str(file.locate())).resolve()
        if not resolved.is_relative_to(root):
            logger.warning("Refusing license file outside distribution %s: %s", name, file)
            return None
        if resolved.stat().st_size > LICENSE_FILE_MAX_BYTES:
            logger.warning("Refusing oversized license file for %s: %s", name, file)
            return None
        text = resolved.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        logger.warning("Failed to read license file for %s: %s", name, e)
        return None
    return text.strip() or None


def _license_text(dist: importlib_metadata.Distribution) -> str | None:
    """Read the license files bundled in the distribution's dist-info.

    Files declared via the PEP 639 License-File field take precedence over
    the filename heuristic. Multiple files (e.g. dual-licensed packages) are
    concatenated with a filename heading each.

    Args:
        dist: Installed distribution.

    Returns:
        License text, or None if no license file is bundled.
    """
    declared = {
        Path(value).name for value in dist.metadata.get_all('License-File', [])
    }
    candidates = [
        file for file in dist.files or []
        if any(part.endswith('.dist-info') for part in file.parts)
        and (file.name in declared or LICENSE_FILE_PATTERN.match(file.name))
    ]
    candidates.sort(key=lambda f: (f.name not in declared, str(f)))

    sections = []
    for file in candidates:
        text = _read_license_file(dist, file)
        if text:
            sections.append(text if len(candidates) == 1 else f'--- {file.name} ---\n\n{text}')
    return '\n\n'.join(sections) or None


def generate_licenses() -> bool:
    """Generate licenses.json from the metadata of installed distributions.

    Returns:
        True if generation was successful, False otherwise.
    """
    licenses_path = get_licenses_path()
    licenses_path.parent.mkdir(parents=True, exist_ok=True)

    licenses_data = []
    seen = set()
    for dist in importlib_metadata.distributions():
        # One unreadable dist-info (e.g. non-UTF-8 metadata of an old
        # install) must not block application startup.
        try:
            meta = dist.metadata
            name = meta.get('Name')
            if not name or _is_vendored(dist):
                continue
            canonical = _canonical_name(name)
            if canonical in EXCLUDED_DISTRIBUTIONS or canonical in seen:
                continue
            seen.add(canonical)

            licenses_data.append({
                'Name': name,
                'License': _license_name(meta),
                'Author': _author(meta),
                'Description': (meta.get('Summary') or '').strip() or None,
                'URL': _sanitize_url(_homepage_url(meta)),
                'LicenseText': _license_text(dist),
            })
        except Exception as e:
            logger.warning("Skipping distribution with unreadable metadata: %s", e)

    licenses_data.sort(key=lambda x: x['Name'].lower())

    # Unique temp file + rename so concurrent Gunicorn workers regenerating
    # at startup never observe a half-written or interleaved file.
    tmp_fd, tmp_name = tempfile.mkstemp(dir=licenses_path.parent, suffix='.tmp')
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(licenses_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, licenses_path)
    except OSError as e:
        logger.error("Failed to write licenses.json: %s", e)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return False

    logger.info("Generated licenses.json with %d packages", len(licenses_data))
    return True


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
    """Load all licenses from the generated and the manual JSON file.

    Returns:
        Combined list of license dictionaries sorted alphabetically by
        package name, or empty list if no files found.
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
