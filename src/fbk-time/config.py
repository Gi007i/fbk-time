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


def _load_secret_key():
    """Load SECRET_KEY from .env file."""
    config_dir = Path(__file__).resolve().parent
    env_path = config_dir / '.env'

    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('SECRET_KEY='):
                    return line.split('=', 1)[1].strip()
        raise ValueError('SECRET_KEY nicht in .env-Datei gefunden')
    except FileNotFoundError:
        raise ValueError(
            f'.env-Datei nicht gefunden in {config_dir} - '
            'Setup erforderlich (python cli/setup.py init)'
        )


_MIN_SECRET_KEY_LENGTH = 32


def _resolve_secret_key():
    """Resolve SECRET_KEY from environment or .env and enforce a length floor."""
    key = os.environ.get('SECRET_KEY') or _load_secret_key()
    if len(key) < _MIN_SECRET_KEY_LENGTH:
        raise ValueError(
            f'SECRET_KEY ist leer oder kuerzer als {_MIN_SECRET_KEY_LENGTH} '
            'Zeichen - Setup erforderlich (python cli/setup.py init)'
        )
    return key


class Config:
    """Production configuration loaded from settings.json."""

    _SETTINGS = _load_settings()
    SYSTEM_SETTINGS = _SETTINGS['system']

    BASE_DIR = Path(__file__).resolve().parent

    SECRET_KEY = _resolve_secret_key()

    _db_path = SYSTEM_SETTINGS['database']['path']
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / _db_path}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    LICENSES_PATH = BASE_DIR / SYSTEM_SETTINGS['licenses']['path']
    MANUAL_LICENSES_PATH = BASE_DIR / SYSTEM_SETTINGS['licenses']['manual_path']

    # The default backup directory (/tmp/fbk-time-backups in settings.json) is
    # a development convenience only. /tmp is world-writable and volatile
    # across reboots, so production deployments must override
    # system.backup.directory with a persistent path outside BASE_DIR.
    BACKUP_DIR = BASE_DIR / SYSTEM_SETTINGS['backup']['directory']

    _runtime_path = Path(SYSTEM_SETTINGS['server']['runtime_path'])
    RUNTIME_DIR = _runtime_path if _runtime_path.is_absolute() else BASE_DIR / _runtime_path

    # Concurrent access for multi-device usage with same account
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'connect_args': {
            'check_same_thread': False,
            'timeout': 30
        }
    }

    _session_hours = SYSTEM_SETTINGS['security']['session']['lifetime_hours']
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_session_hours)

    # Idle timeout: log out after this period without any request. Enforced
    # server-side per request. A value of 0 disables the idle check and
    # leaves only the absolute PERMANENT_SESSION_LIFETIME in effect.
    _idle_minutes = SYSTEM_SETTINGS['security']['session']['idle_timeout_minutes']
    if _idle_minutes < 0:
        raise ValueError(
            'security.session.idle_timeout_minutes darf nicht negativ sein'
        )
    SESSION_IDLE_TIMEOUT = timedelta(minutes=_idle_minutes)

    # Lead time before idle expiry at which the client shows the session
    # warning dialog with the option to extend. Purely client-side UX; the
    # server remains the sole authority on expiry. Must be a positive value
    # smaller than the idle window (when the idle check is enabled).
    _idle_warning_seconds = SYSTEM_SETTINGS['security']['session']['idle_warning_seconds']
    if _idle_warning_seconds <= 0:
        raise ValueError(
            'security.session.idle_warning_seconds muss groesser als 0 sein'
        )
    if _idle_minutes > 0 and _idle_warning_seconds >= _idle_minutes * 60:
        raise ValueError(
            'security.session.idle_warning_seconds muss kleiner als das '
            'Leerlauf-Zeitfenster (idle_timeout_minutes) sein'
        )
    SESSION_IDLE_WARNING_SECONDS = _idle_warning_seconds

    _remember_days = SYSTEM_SETTINGS['security']['session']['remember_cookie_days']
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=_remember_days)

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    JSON_AS_ASCII = False

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    HOST = SYSTEM_SETTINGS['server']['host']
    PORT = SYSTEM_SETTINGS['server']['port']

    # Argon2id settings (RFC 9106 LOW_MEMORY profile)
    ARGON2_TIME_COST = SYSTEM_SETTINGS['security']['argon2']['time_cost']
    ARGON2_MEMORY_COST = SYSTEM_SETTINGS['security']['argon2']['memory_cost']
    ARGON2_PARALLELISM = SYSTEM_SETTINGS['security']['argon2']['parallelism']
    ARGON2_HASH_LENGTH = 32
    ARGON2_SALT_LENGTH = 16

    DEBUG = False
    TESTING = False

    @classmethod
    def get_system_setting(cls, *keys):
        """Get nested system setting by keys.

        Example: Config.get_system_setting('security', 'session', 'lifetime_hours')
        """
        value = cls.SYSTEM_SETTINGS
        for key in keys:
            value = value[key]
        return value

    @staticmethod
    def load_settings_from_file():
        """Reload settings directly from file."""
        return _load_settings()

    @classmethod
    def reload_settings(cls):
        """Reload settings from file and update class variables."""
        cls._SETTINGS = _load_settings()
        cls.SYSTEM_SETTINGS = cls._SETTINGS['system']

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

        for rel_path in paths_to_ensure:
            dir_path = (base_dir / rel_path).parent
            if not dir_path.exists():
                dir_path.mkdir(mode=0o755, parents=True, exist_ok=True)

        # Backup archives contain SECRET_KEY and Argon2id password hashes.
        # The directory must stay outside BASE_DIR so a misconfigured Nginx
        # alias or document root cannot serve the archives. 0o700 protects
        # against other local users. mkdir's mode is only honoured on fresh
        # directories, so harden existing ones explicitly. No-op on Windows.
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

        existed = backup_dir.exists()
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if existed and os.name == 'posix':
            try:
                backup_dir.chmod(0o700)
            except OSError as exc:
                app.logger.warning(
                    f"Could not tighten permissions on backup dir "
                    f"{backup_dir}: {exc}"
                )

        cls.RUNTIME_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)

        if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
            db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

            conn = sqlite3.connect(db_path)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA cache_size=10000')
            conn.execute('PRAGMA temp_store=MEMORY')
            conn.close()


def get_config():
    """Return the production configuration class."""
    return Config
