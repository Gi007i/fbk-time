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


class Config:
    """Production configuration loaded from settings.json."""

    _SETTINGS = _load_settings()
    SYSTEM_SETTINGS = _SETTINGS['system']

    BASE_DIR = Path(__file__).resolve().parent

    SECRET_KEY = os.environ.get('SECRET_KEY') or _load_secret_key()

    _db_path = SYSTEM_SETTINGS['database']['path']
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / _db_path}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    LICENSES_PATH = BASE_DIR / SYSTEM_SETTINGS['licenses']['path']
    MANUAL_LICENSES_PATH = BASE_DIR / SYSTEM_SETTINGS['licenses']['manual_path']

    # Concurrent access for multi-device usage with same account
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'connect_args': {
            'check_same_thread': False,
            'timeout': 30
        }
    }

    # Session settings
    _session_hours = SYSTEM_SETTINGS['security']['session']['lifetime_hours']
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_session_hours)

    _remember_days = SYSTEM_SETTINGS['security']['session']['remember_cookie_days']
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=_remember_days)

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

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
