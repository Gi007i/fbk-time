"""Settings manager for runtime-configurable system settings.

Provides thread-safe access to settings stored in the database with caching.
Single source of truth: settings-template.json defines all settings.
"""

import json
import threading
import time
from pathlib import Path

VERSION_CHECK_INTERVAL = 1.0
BASE_DIR = Path(__file__).resolve().parent.parent


def _get_template_path():
    """Get template path from settings.json."""
    settings_path = BASE_DIR / 'settings.json'
    with open(settings_path, 'r', encoding='utf-8') as f:
        settings = json.load(f)
    return BASE_DIR / settings['system']['settings_template_path']


def _load_template():
    """Load settings template from JSON file.

    Returns:
        Dict with full template data (key -> {default, type, category}).
    """
    template_path = _get_template_path()
    with open(template_path, 'r', encoding='utf-8') as f:
        return json.load(f)


_TEMPLATE = _load_template()

# key -> (category, type)
SETTING_DEFINITIONS = {
    key: (data['category'], data['type'])
    for key, data in _TEMPLATE.items()
}


class SettingsManager:
    """Thread-safe settings cache with DB backend.

    All settings must be loaded before use. Raises KeyError if accessing
    uninitialized settings to prevent silent failures.
    """

    SETTING_DEFINITIONS = SETTING_DEFINITIONS

    def __init__(self):
        self._cache = {}
        self._lock = threading.RLock()
        self._local_version = 0
        self._last_version_check = 0.0

    @staticmethod
    def _staged_changes() -> dict:
        # Bound to the session, not the manager: staged values share the
        # fate of the uncommitted rows and vanish with a discarded session.
        from core.db import db
        return db.session.info.setdefault('staged_settings', {})

    def _check_version(self):
        """Check if local cache version matches database version.

        Reloads cache if version mismatch detected. Throttled to max
        one database check per VERSION_CHECK_INTERVAL seconds.
        """
        now = time.time()
        if now - self._last_version_check < VERSION_CHECK_INTERVAL:
            return

        self._last_version_check = now

        from sqlalchemy import select

        from core.db import db
        from modules.settings.models import Setting

        # Overwrite any cached instance: without populate_existing the
        # identity map would mask version bumps from other workers.
        db_setting = db.session.scalars(
            select(Setting)
            .filter_by(key='cache_version')
            .execution_options(populate_existing=True)
        ).first()
        if db_setting:
            db_version = db_setting.get_typed_value()
            if db_version != self._local_version:
                self._reload_cache()

    def _reload_cache(self):
        """Reload all settings from database into cache."""
        from sqlalchemy import select

        from core.db import db
        from modules.settings.models import Setting

        settings = db.session.scalars(select(Setting)).all()
        self._cache.clear()
        for setting in settings:
            self._cache[setting.key] = setting.get_typed_value()
        self._local_version = self._cache.get('cache_version', 0)

    def refresh(self):
        """Reload the cache from the database, bypassing the check throttle.

        For callers that already know another worker committed a change and
        must not act on a cache checked less than a throttle interval ago.
        """
        with self._lock:
            self._last_version_check = time.time()
            self._reload_cache()

    def get(self, key):
        """Get setting value from cache.

        Args:
            key: Setting key name.

        Returns:
            Typed setting value.

        Raises:
            KeyError: If setting not found (app misconfigured).
        """
        with self._lock:
            self._check_version()
            if key not in self._cache:
                raise KeyError(
                    f"Setting '{key}' not found. "
                    "Ensure settings are loaded from database."
                )
            return self._cache[key]

    def set(self, key, value):
        """Stage a setting change in the database session.

        Does not commit and leaves the cache untouched. Call flush() after
        all changes; the cache follows only a successful commit.

        Args:
            key: Setting key name.
            value: New value (type must match definition).

        Raises:
            KeyError: If key not in SETTING_DEFINITIONS.
        """
        if key not in self.SETTING_DEFINITIONS:
            raise KeyError(f"Unknown setting key: {key}")

        from core.db import db
        from modules.settings.models import Setting

        with self._lock:
            setting = db.session.get(Setting, key)
            if setting:
                setting.set_typed_value(value)
            else:
                category, data_type = self.SETTING_DEFINITIONS[key]
                from modules.settings.models import SettingDataType
                setting = Setting(
                    key=key,
                    value=str(value),
                    data_type=SettingDataType(data_type),
                    category=category
                )
                setting.set_typed_value(value)
                db.session.add(setting)

            self._staged_changes()[key] = value

    def flush(self):
        """Commit staged changes and increment cache version once.

        The version bump notifies other workers. A failed commit rolls back,
        discards the staged values and marks the cache stale.

        Raises:
            Exception: Whatever the commit raised.
        """
        from core.db import db
        from modules.settings.models import Setting

        with self._lock:
            staged = self._staged_changes()
            if not staged:
                return

            try:
                new_version = None
                cache_current = True
                # populate_existing: an instance loaded earlier in this session
                # would hide a bump committed by another worker meanwhile.
                version_setting = db.session.get(
                    Setting, 'cache_version', populate_existing=True
                )
                if version_setting:
                    db_version = version_setting.get_typed_value()
                    cache_current = db_version == self._local_version
                    new_version = db_version + 1
                    version_setting.set_typed_value(new_version)
                db.session.commit()
            except Exception:
                db.session.rollback()
                staged.clear()
                # A read during staging may have autoflushed uncommitted rows
                # into the cache; a version no database row carries forces a reload.
                self._local_version = -1
                self._last_version_check = 0.0
                raise

            if not cache_current:
                # Another worker committed meanwhile; merging only the staged
                # values would hide its changes from this worker for good.
                staged.clear()
                self._last_version_check = time.time()
                self._reload_cache()
                return

            self._cache.update(staged)
            staged.clear()
            if new_version is not None:
                self._cache['cache_version'] = new_version
                self._local_version = new_version

    def load_all(self):
        """Load all settings from database into cache.

        Should be called during app initialization within app context.
        """
        from sqlalchemy import select

        from core.db import db
        from modules.settings.models import Setting

        with self._lock:
            settings = db.session.scalars(select(Setting)).all()
            for setting in settings:
                self._cache[setting.key] = setting.get_typed_value()
            self._local_version = self._cache.get('cache_version', 0)

    def seed_defaults(self):
        """Upsert default settings from the template into the database.

        Idempotent: inserts missing keys (fresh install or newer template)
        and leaves existing values untouched.

        Returns:
            List of keys that were newly inserted.
        """
        from sqlalchemy import select

        from core.db import db
        from modules.settings.models import Setting, SettingDataType

        inserted_keys = []

        with self._lock:
            existing_keys = set(db.session.scalars(select(Setting.key)).all())

            for key, data in _TEMPLATE.items():
                if key in existing_keys:
                    continue
                setting = Setting(
                    key=key,
                    value='',
                    data_type=SettingDataType(data['type']),
                    category=data['category']
                )
                setting.set_typed_value(data['default'])
                db.session.add(setting)
                self._cache[key] = data['default']
                inserted_keys.append(key)

            if inserted_keys:
                db.session.commit()

            self._local_version = self._cache.get('cache_version', 0)

        return inserted_keys


settings_manager = SettingsManager()
