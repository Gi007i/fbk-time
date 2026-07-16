"""Application timezone helper.

Resolves the active timezone from the runtime-configurable
``app_timezone`` setting. All UTC↔local conversions in templates,
exports and CLI tools route through ``get_app_timezone`` so the
admin-facing setting becomes the single source of truth.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app


SUPPORTED_TIMEZONES = (
    'Europe/Berlin',
    'Europe/Vienna',
    'Europe/Zurich',
    'Europe/Amsterdam',
    'Europe/Brussels',
    'Europe/Paris',
    'Europe/London',
    'Europe/Madrid',
    'Europe/Rome',
    'Europe/Warsaw',
    'UTC',
)


def get_app_timezone() -> ZoneInfo:
    """Return the configured application timezone.

    Reads the cached ``app_timezone`` setting and resolves it to a
    ``ZoneInfo``. The form layer constrains input to ``SUPPORTED_TIMEZONES``,
    but if the stored value is unknown to the system's timezone database the
    lookup falls back to UTC so conversions never raise.
    """
    from core.settings_manager import settings_manager

    name = settings_manager.get('app_timezone')
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        current_app.logger.warning(
            f"Unknown app_timezone {name!r}, falling back to UTC"
        )
        return ZoneInfo('UTC')
