"""Settings management services.

Provides business logic for user and system settings updates.
"""

from datetime import date
from typing import Optional

from flask_login import current_user

from core.extensions import db
from core.settings_manager import settings_manager


def get_date_format_choices() -> list[tuple[str, str]]:
    """Generate date format choices with current year example.

    Returns:
        List of (value, label) tuples for date format options.
    """
    year = date.today().year
    return [
        ('DD.MM.YYYY', f'DD.MM.YYYY (z.B. 25.12.{year})'),
        ('YYYY-MM-DD', f'YYYY-MM-DD (z.B. {year}-12-25)')
    ]


def update_user_settings(
    holiday_region: str,
    theme: str,
    date_format: str,
    pagination: int,
    default_text_color: str
) -> None:
    """Update current user's personal settings.

    Args:
        holiday_region: Holiday region code.
        theme: Theme preference (light/dark/auto).
        date_format: Date format preference.
        pagination: Items per page (0 = show all).
        default_text_color: Default text color for new categories.
    """
    current_user.holiday_region = holiday_region
    current_user.theme = theme
    current_user.date_format = date_format
    current_user.items_per_page = pagination
    current_user.default_text_color = default_text_color.upper()

    db.session.commit()


def update_system_settings(
    lockout_threshold: int,
    lockout_duration_minutes: int,
    lockout_delay_enabled: bool,
    lockout_delay_base_seconds: int,
    lockout_delay_max_seconds: int,
    lockout_attempt_retention_hours: int,
    lockout_cleanup_enabled: bool,
    lockout_cleanup_interval_hours: int,
    password_min_length: int,
    password_max_length: int,
    password_require_uppercase: bool,
    password_require_lowercase: bool,
    password_require_numbers: bool,
    password_require_symbols: bool,
    password_force_change_on_first_login: bool,
    inactive_account_auto_disable: bool,
    inactive_account_days: int,
    self_registration_enabled: bool,
    operation_mode: str,
    user_default_theme: str,
    user_default_date_format: str,
    user_default_items_per_page: int,
    user_default_holiday_region: str,
    user_default_text_color: str
) -> None:
    """Update all system settings.

    Args:
        lockout_threshold: Failed attempts before lockout.
        lockout_duration_minutes: How long account stays locked.
        lockout_delay_enabled: Whether progressive delay is enabled.
        lockout_delay_base_seconds: Base delay in seconds.
        lockout_delay_max_seconds: Maximum delay cap.
        lockout_attempt_retention_hours: Hours to retain attempt records.
        lockout_cleanup_enabled: Whether auto-cleanup is enabled.
        lockout_cleanup_interval_hours: Cleanup interval.
        password_min_length: Minimum password length.
        password_max_length: Maximum password length.
        password_require_uppercase: Require uppercase letters.
        password_require_lowercase: Require lowercase letters.
        password_require_numbers: Require numbers.
        password_require_symbols: Require special characters.
        password_force_change_on_first_login: Force change on first login.
        inactive_account_auto_disable: Auto-disable inactive accounts.
        inactive_account_days: Days of inactivity before disable.
        self_registration_enabled: Allow self-registration.
        operation_mode: single_user or multi_user.
        user_default_theme: Default theme for new users.
        user_default_date_format: Default date format for new users.
        user_default_items_per_page: Default pagination for new users.
        user_default_holiday_region: Default holiday region for new users.
        user_default_text_color: Default text color for new users.
    """
    # Lockout settings
    settings_manager.set('lockout_threshold', lockout_threshold)
    settings_manager.set('lockout_duration_minutes', lockout_duration_minutes)
    settings_manager.set('lockout_delay_enabled', lockout_delay_enabled)
    settings_manager.set('lockout_delay_base_seconds', lockout_delay_base_seconds or 0)
    settings_manager.set('lockout_delay_max_seconds', lockout_delay_max_seconds or 0)
    settings_manager.set('lockout_attempt_retention_hours', lockout_attempt_retention_hours)
    settings_manager.set('lockout_cleanup_enabled', lockout_cleanup_enabled)
    settings_manager.set('lockout_cleanup_interval_hours', lockout_cleanup_interval_hours or 1)

    # Password policy
    settings_manager.set('password_min_length', password_min_length)
    settings_manager.set('password_max_length', password_max_length)
    settings_manager.set('password_require_uppercase', password_require_uppercase)
    settings_manager.set('password_require_lowercase', password_require_lowercase)
    settings_manager.set('password_require_numbers', password_require_numbers)
    settings_manager.set('password_require_symbols', password_require_symbols)
    settings_manager.set('password_force_change_on_first_login', password_force_change_on_first_login)

    # Inactive account
    settings_manager.set('inactive_account_auto_disable', inactive_account_auto_disable)
    settings_manager.set('inactive_account_days', inactive_account_days or 90)

    # Registration
    settings_manager.set('self_registration_enabled', self_registration_enabled)

    # Operation mode - handle mode switch
    _handle_operation_mode_change(operation_mode)

    # User defaults
    settings_manager.set('user_default_theme', user_default_theme)
    settings_manager.set('user_default_date_format', user_default_date_format)
    settings_manager.set('user_default_items_per_page', user_default_items_per_page)
    settings_manager.set('user_default_holiday_region', user_default_holiday_region)
    settings_manager.set('user_default_text_color', user_default_text_color.upper())

    settings_manager.flush()


def _handle_operation_mode_change(new_mode: str) -> None:
    """Handle operation mode switch.

    When switching to single_user mode:
    - Invalidates all USER sessions
    - Sets all active USER accounts to MANAGED

    Args:
        new_mode: New operation mode.
    """
    old_mode = settings_manager.get('operation_mode')

    if old_mode == new_mode:
        return

    settings_manager.set('operation_mode', new_mode)

    if new_mode == 'single_user':
        current_version = settings_manager.get('user_session_version')
        settings_manager.set('user_session_version', current_version + 1)

        from modules.auth.models import User, UserRole, UserStatus
        User.query.filter(
            User.role == UserRole.USER,
            User.status == UserStatus.ACTIVE
        ).update({User.status: UserStatus.MANAGED})
        db.session.commit()


def set_user_theme(theme: str) -> Optional[str]:
    """Set current user's theme.

    Args:
        theme: Theme value (light/dark/auto).

    Returns:
        Error message if invalid, None on success.
    """
    if theme not in ('light', 'dark', 'auto'):
        return 'Invalid theme'

    current_user.theme = theme
    db.session.commit()
    return None


def get_current_settings() -> dict:
    """Get current user's settings as dict.

    Returns:
        Dict with current user's settings.
    """
    return {
        'holiday_region': current_user.holiday_region,
        'theme': current_user.theme,
        'date_format': current_user.date_format,
        'pagination': current_user.items_per_page,
        'default_text_color': current_user.default_text_color
    }
