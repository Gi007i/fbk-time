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


# Load template once at module import
_TEMPLATE = _load_template()

# Generate SETTING_DEFINITIONS from template: key -> (category, type)
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
        self._initialized = False
        self._local_version = 0
        self._last_version_check = 0.0
        self._pending_changes = False

    def _check_version(self):
        """Check if local cache version matches database version.

        Reloads cache if version mismatch detected. Throttled to max
        one database check per VERSION_CHECK_INTERVAL seconds.
        """
        now = time.time()
        if now - self._last_version_check < VERSION_CHECK_INTERVAL:
            return

        self._last_version_check = now

        from modules.settings.models import Setting

        db_setting = Setting.query.filter_by(key='cache_version').first()
        if db_setting:
            db_version = db_setting.get_typed_value()
            if db_version != self._local_version:
                self._reload_cache()

    def _reload_cache(self):
        """Reload all settings from database into cache."""
        from modules.settings.models import Setting

        settings = Setting.query.all()
        self._cache.clear()
        for setting in settings:
            self._cache[setting.key] = setting.get_typed_value()
        self._local_version = self._cache.get('cache_version', 0)

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
        """Update setting in database and cache.

        Does not commit immediately. Call flush() after all changes.

        Args:
            key: Setting key name.
            value: New value (type must match definition).

        Raises:
            KeyError: If key not in SETTING_DEFINITIONS.
        """
        if key not in self.SETTING_DEFINITIONS:
            raise KeyError(f"Unknown setting key: {key}")

        from core.extensions import db
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

            self._cache[key] = value
            self._pending_changes = True

    def flush(self):
        """Commit pending changes and increment cache version once.

        Call this after a batch of set() calls to persist changes
        and notify other workers.
        """
        from core.extensions import db
        from modules.settings.models import Setting

        with self._lock:
            if not self._pending_changes:
                return

            # Increment cache_version once for all changes
            version_setting = db.session.get(Setting, 'cache_version')
            if version_setting:
                new_version = version_setting.get_typed_value() + 1
                version_setting.set_typed_value(new_version)
                self._cache['cache_version'] = new_version
                self._local_version = new_version

            db.session.commit()
            self._pending_changes = False

    def load_all(self):
        """Load all settings from database into cache.

        Should be called during app initialization within app context.
        """
        from modules.settings.models import Setting

        with self._lock:
            settings = Setting.query.all()
            for setting in settings:
                self._cache[setting.key] = setting.get_typed_value()
            self._local_version = self._cache.get('cache_version', 0)
            self._initialized = True

    def seed_defaults(self):
        """Write default settings from template into empty database.

        Reads defaults from data/settings-template.json.
        """
        from core.extensions import db
        from modules.settings.models import Setting, SettingDataType

        with self._lock:
            for key, data in _TEMPLATE.items():
                setting = Setting(
                    key=key,
                    value='',
                    data_type=SettingDataType(data['type']),
                    category=data['category']
                )
                setting.set_typed_value(data['default'])
                db.session.add(setting)
                self._cache[key] = data['default']

            db.session.commit()
            self._local_version = self._cache.get('cache_version', 0)
            self._initialized = True

    def is_initialized(self):
        """Check if settings have been loaded."""
        return self._initialized

    def get_all_by_category(self, category):
        """Get all settings for a category.

        Args:
            category: Category name.

        Returns:
            Dict of key -> value for the category.
        """
        with self._lock:
            self._check_version()
            return {
                key: self._cache[key]
                for key, (cat, _) in self.SETTING_DEFINITIONS.items()
                if cat == category and key in self._cache
            }


settings_manager = SettingsManager()
