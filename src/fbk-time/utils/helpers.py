"""Helper functions.

Provides date formatting utilities.
"""

from datetime import date


def format_date_for_user(d: date, short: bool = False, include_time: bool = False) -> str:
    """Format date according to current user's preference.

    Args:
        d: Date to format.
        short: If True, omit year (e.g., '25.12.' or '12-25').
        include_time: If True, append time (requires datetime object).

    Returns:
        Formatted date string based on user's date_format setting.
    """
    from flask_login import current_user

    if not d:
        return ''

    if current_user.is_authenticated:
        fmt_setting = current_user.date_format
    else:
        fmt_setting = 'DD.MM.YYYY'

    if fmt_setting == 'YYYY-MM-DD':
        fmt = '%m-%d' if short else '%Y-%m-%d'
    else:
        fmt = '%d.%m.' if short else '%d.%m.%Y'

    if include_time:
        fmt += ' %H:%M'

    return d.strftime(fmt)
