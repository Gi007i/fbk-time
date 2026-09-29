"""Configuration module.

Loads system configuration from settings.json.
Runtime-configurable settings (lockout, password policy, etc.) are stored in the database.
"""

import os
import json
import sqlite3
from datetime import timedelta
from pathlib import Path


def _load_settings():
    """Load settings from settings.json file."""
    config_dir = Path(__file__).resolve().parent
    settings_path = config_dir / 'settings.json'

    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise ValueError(
            f'settings.json nicht gefunden in {config_dir} - '
            'Setup erforderlich (python cli/setup.py init)'
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"Fehler beim Laden von settings.json: {e}")


def _load_env_file():
    """Return the ``NAME=value`` lines of the .env file as a dict."""
    config_dir = Path(__file__).resolve().parent
    env_path = config_dir / '.env'

    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            values = {}
            for line in f:
                name, separator, value = line.partition('=')
                if separator:
                    values[name.strip()] = value.strip()
            return values
    except FileNotFoundError:
        raise ValueError(
            f'.env-Datei nicht gefunden in {config_dir} - '
            'Setup erforderlich (python cli/setup.py init)'
        )


_MIN_SECRET_KEY_LENGTH = 32


def _open_no_symlink(path, flags, link_message, mode=0o600):
    """Open a path with O_NOFOLLOW and return the file descriptor (POSIX).

    Working on the descriptor instead of the path closes the window between
    a symlink check and chmod/stat. A link planted in that window is
    reported with the same message as the up-front check.
    """
    try:
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    except OSError as exc:
        # Linux reports a final symlink as ELOOP, or as ENOTDIR together
        # with O_DIRECTORY; the link itself is the decisive fact.
        if os.path.islink(path):
            raise RuntimeError(link_message) from exc
        raise


def _resolve_secret_keys():
    """Resolve SECRET_KEY and SECRET_KEY_FALLBACKS and enforce a length floor.

    Both come from the environment when SECRET_KEY is set there, otherwise
    from the .env file, so the keys of one rotation never mix sources.
    SECRET_KEY_FALLBACKS is optional and holds comma-separated old keys.

    Returns:
        Tuple of (current key, list of fallback keys).
    """
    if os.environ.get('SECRET_KEY'):
        key = os.environ['SECRET_KEY']
        fallbacks_raw = os.environ.get('SECRET_KEY_FALLBACKS', '')
    else:
        env = _load_env_file()
        if 'SECRET_KEY' not in env:
            raise ValueError('SECRET_KEY nicht in .env-Datei gefunden')
        key = env['SECRET_KEY']
        fallbacks_raw = env.get('SECRET_KEY_FALLBACKS', '')

    if len(key) < _MIN_SECRET_KEY_LENGTH:
        raise ValueError(
            f'SECRET_KEY ist leer oder kürzer als {_MIN_SECRET_KEY_LENGTH} '
            'Zeichen - Setup erforderlich (python cli/setup.py init)'
        )

    fallbacks = []
    if fallbacks_raw.strip():
        fallbacks = [entry.strip() for entry in fallbacks_raw.split(',')]
        if any(len(entry) < _MIN_SECRET_KEY_LENGTH for entry in fallbacks):
            raise ValueError(
                'SECRET_KEY_FALLBACKS enthält einen leeren oder kürzeren '
                f'Schlüssel als {_MIN_SECRET_KEY_LENGTH} Zeichen'
            )
    return key, fallbacks

_REQUIRED_SYSTEM_SETTINGS = (
    ('database', 'path'),
    ('logs', 'access_log'),
    ('logs', 'error_log'),
    ('licenses', 'path'),
    ('licenses', 'manual_path'),
    ('server', 'host'),
    ('server', 'port'),
    ('server', 'runtime_path'),
    ('backup', 'directory'),
    ('security', 'session', 'lifetime_hours'),
    ('security', 'session', 'idle_timeout_minutes'),
    ('security', 'session', 'idle_warning_seconds'),
    ('security', 'session', 'remember_cookie_days'),
    ('security', 'argon2', 'time_cost'),
    ('security', 'argon2', 'memory_cost'),
    ('security', 'argon2', 'parallelism'),
)


def _validate_system_settings(system):
    """Fail with a clear message when settings.json omits a required field."""
    missing = []
    for path in _REQUIRED_SYSTEM_SETTINGS:
        node = system
        for key in path:
            if not isinstance(node, dict) or key not in node:
                missing.append('.'.join(path))
                break
            node = node[key]
    if missing:
        raise ValueError(
            'settings.json: fehlende Pflichtfelder unter "system": '
            + ', '.join(missing)
            + ' - Setup oder Upgrade erforderlich.'
        )


class Config:
    """Production configuration loaded from settings.json."""

    _SETTINGS = _load_settings()
    SYSTEM_SETTINGS = _SETTINGS.get('system', {})
    _validate_system_settings(SYSTEM_SETTINGS)

    BASE_DIR = Path(__file__).resolve().parent

    SECRET_KEY, SECRET_KEY_FALLBACKS = _resolve_secret_keys()

    _db_path = SYSTEM_SETTINGS['database']['path']
    DATABASE_URI = f"sqlite:///{BASE_DIR / _db_path}"

    LICENSES_PATH = BASE_DIR / SYSTEM_SETTINGS['licenses']['path']
    MANUAL_LICENSES_PATH = BASE_DIR / SYSTEM_SETTINGS['licenses']['manual_path']

    BACKUP_DIR = BASE_DIR / SYSTEM_SETTINGS['backup']['directory']

    _runtime_path = Path(SYSTEM_SETTINGS['server']['runtime_path'])
    RUNTIME_DIR = _runtime_path if _runtime_path.is_absolute() else BASE_DIR / _runtime_path

    # Concurrent access for multi-device usage with same account
    DATABASE_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'connect_args': {
            'check_same_thread': False,
            'timeout': 30
        }
    }

    _session_hours = SYSTEM_SETTINGS['security']['session']['lifetime_hours']
    if _session_hours <= 0:
        raise ValueError(
            'security.session.lifetime_hours muss größer als 0 sein'
        )
    # __Host- prefix: accepted only over a Secure, host-only origin, which
    # blocks cookie injection from subdomains or insecure siblings.
    SESSION_COOKIE_NAME = '__Host-session'
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_session_hours)

    # OWASP requires an idle timeout next to the absolute limit.
    _idle_minutes = SYSTEM_SETTINGS['security']['session']['idle_timeout_minutes']
    if _idle_minutes <= 0:
        raise ValueError(
            'security.session.idle_timeout_minutes muss größer als 0 sein'
        )
    # The absolute lifetime caps the idle window and cannot be extended; were
    # it the smaller of the two, every keep-alive would be a no-op.
    if _idle_minutes * 60 >= _session_hours * 3600:
        raise ValueError(
            'security.session.lifetime_hours muss größer als das '
            'Leerlauf-Zeitfenster (idle_timeout_minutes) sein'
        )
    SESSION_IDLE_TIMEOUT = timedelta(minutes=_idle_minutes)

    # Client-side warning lead time only; the server alone decides expiry.
    _idle_warning_seconds = SYSTEM_SETTINGS['security']['session']['idle_warning_seconds']
    # WCAG 2.2.1 requires at least 20 seconds to react to a timeout warning.
    if _idle_warning_seconds < 20:
        raise ValueError(
            'security.session.idle_warning_seconds muss mindestens 20 sein'
        )
    if _idle_warning_seconds >= _idle_minutes * 60:
        raise ValueError(
            'security.session.idle_warning_seconds muss kleiner als das '
            'Leerlauf-Zeitfenster (idle_timeout_minutes) sein'
        )
    SESSION_IDLE_WARNING_SECONDS = _idle_warning_seconds

    _remember_days = SYSTEM_SETTINGS['security']['session']['remember_cookie_days']
    if _remember_days <= 0:
        raise ValueError(
            'security.session.remember_cookie_days muss größer als 0 sein'
        )
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=_remember_days)

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    # Caps a whole url-encoded form body and each multipart text field. The
    # longest field holds 1000 characters, at most 9 KB percent-encoded, so
    # 64 KiB leaves ample headroom (JSON bodies fall under MAX_CONTENT_LENGTH).
    MAX_FORM_MEMORY_SIZE = 64 * 1024
    # Multipart part count; the largest form (system settings) has fewer
    # than 50 fields.
    MAX_FORM_PARTS = 200

    # TRUSTED_HOSTS stays unset: host header validation belongs to the
    # reverse proxy's catch-all/default server.

    HOST = SYSTEM_SETTINGS['server']['host']
    PORT = SYSTEM_SETTINGS['server']['port']

    # RFC 9106 LOW_MEMORY profile
    ARGON2_TIME_COST = SYSTEM_SETTINGS['security']['argon2']['time_cost']
    ARGON2_MEMORY_COST = SYSTEM_SETTINGS['security']['argon2']['memory_cost']
    ARGON2_PARALLELISM = SYSTEM_SETTINGS['security']['argon2']['parallelism']
    ARGON2_HASH_LENGTH = 32
    ARGON2_SALT_LENGTH = 16

    DEBUG = False
    TESTING = False

    @classmethod
    def init_app(cls, app):
        """Initialize application directories and database settings.

        Creates required directories from configured paths if they don't exist
        and enables WAL mode on SQLite database for concurrent multi-device access.
        """
        base_dir = Path(__file__).resolve().parent

        paths_to_ensure = [
            cls.SYSTEM_SETTINGS['database']['path'],
            cls.SYSTEM_SETTINGS['logs']['access_log'],
            cls.SYSTEM_SETTINGS['logs']['error_log'],
        ]

        # 0o750: database and logs hold personal data. fchmod runs always,
        # since mkdir's mode only applies to new directories. Symlinks are
        # refused: a planted link would redirect chmod and later writes of a
        # CLI running as root.
        for rel_path in paths_to_ensure:
            dir_path = (base_dir / rel_path).parent
            link_message = (
                f'Verzeichnis ist ein Symlink ({dir_path}). '
                f'Pfade in settings.json müssen auf echte Verzeichnisse zeigen.'
            )
            if dir_path.is_symlink():
                raise RuntimeError(link_message)
            dir_path.mkdir(mode=0o750, parents=True, exist_ok=True)
            if os.name == 'posix':
                fd = _open_no_symlink(
                    dir_path, os.O_RDONLY | os.O_DIRECTORY, link_message
                )
                try:
                    os.fchmod(fd, 0o750)
                finally:
                    os.close(fd)

        # Archives contain SECRET_KEY and password hashes: outside BASE_DIR so
        # a misconfigured Nginx alias cannot serve them, 0o700 against other
        # local users.
        backup_dir = base_dir / cls.SYSTEM_SETTINGS['backup']['directory']
        resolved_backup_dir = backup_dir.resolve()
        resolved_base_dir = base_dir.resolve()
        if resolved_backup_dir.is_relative_to(resolved_base_dir):
            raise RuntimeError(
                f'BACKUP_DIR darf nicht innerhalb von BASE_DIR liegen '
                f'(BASE_DIR={resolved_base_dir}, BACKUP_DIR={resolved_backup_dir}). '
                f'system.backup.directory in settings.json auf einen Pfad '
                f'außerhalb von {resolved_base_dir} setzen.'
            )

        # A directory planted by another local user is refused rather than
        # receiving archives.
        backup_link_message = (
            f'BACKUP_DIR ist ein Symlink ({backup_dir}). '
            f'system.backup.directory muss auf ein echtes Verzeichnis zeigen.'
        )
        if backup_dir.is_symlink():
            raise RuntimeError(backup_link_message)

        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        if os.name == 'posix':
            fd = _open_no_symlink(
                backup_dir, os.O_RDONLY | os.O_DIRECTORY, backup_link_message
            )
            try:
                info = os.fstat(fd)
                if info.st_uid != os.geteuid():
                    raise RuntimeError(
                        f'BACKUP_DIR gehört einem anderen Benutzer '
                        f'(uid={info.st_uid}, erwartet uid={os.geteuid()}): {backup_dir}. '
                        f'Die Archive enthalten SECRET_KEY und Passwort-Hashes.'
                    )
                try:
                    os.fchmod(fd, 0o700)
                except OSError as exc:
                    raise RuntimeError(
                        f'Rechte auf BACKUP_DIR ({backup_dir}) konnten nicht auf 0700 '
                        f'gesetzt werden: {exc}'
                    ) from exc
            finally:
                os.close(fd)

        cls.RUNTIME_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)

        if 'sqlite' in app.config['DATABASE_URI']:
            db_path = app.config['DATABASE_URI'].replace('sqlite:///', '')

            # Same reasoning as for the directories: connect and chmod would
            # act on the link target.
            db_link_message = (
                f'Datenbankdatei ist ein Symlink ({db_path}). '
                f'system.database.path muss auf eine echte Datei zeigen.'
            )
            if Path(db_path).is_symlink():
                raise RuntimeError(db_link_message)

            # Created here rather than by sqlite3.connect, which would use the
            # process umask and commonly leave the file world-readable;
            # fchmod also tightens a file from an earlier installation.
            if os.name == 'posix':
                fd = _open_no_symlink(
                    db_path, os.O_RDWR | os.O_CREAT, db_link_message, 0o600
                )
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)

            # The engine's connect handler applies the same PRAGMAs per
            # connection.
            conn = sqlite3.connect(db_path)
            try:
                conn.execute('PRAGMA journal_mode=WAL')
            finally:
                conn.close()


def get_config():
    """Return the production configuration class."""
    return Config
