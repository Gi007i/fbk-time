"""Helper functions.

Provides input sanitization, date formatting, and utility functions.
"""

from datetime import date, timedelta
from typing import Optional


def sanitize_input(value: Optional[str], max_length: int = 100) -> str:
    """
    Sanitize user input by stripping whitespace and limiting length.

    Args:
        value: Input string to sanitize.
        max_length: Maximum allowed length.

    Returns:
        Sanitized string or empty string if None.
    """
    if value:
        return str(value).strip()[:max_length]
    return ''


def format_date_for_user(d: date, short: bool = False, include_time: bool = False) -> str:
    """
    Format date according to current user's preference.

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


def calculate_working_days(
    start_date: date,
    end_date: date,
    holidays: Optional[list] = None
) -> int:
    """
    Calculate number of working days between two dates.

    Excludes weekends (Saturday, Sunday) and optional holidays.

    Args:
        start_date: Start of period.
        end_date: End of period.
        holidays: List of holiday dates to exclude.

    Returns:
        Number of working days.
    """
    if not start_date or not end_date:
        return 0

    if start_date > end_date:
        return 0

    holidays = holidays or []
    holiday_set = set(holidays)
    working_days = 0

    current = start_date
    while current <= end_date:
        if current.weekday() < 5 and current not in holiday_set:
            working_days += 1
        current += timedelta(days=1)

    return working_days


def parse_date(date_string: str, format_string: str = 'DD.MM.YYYY') -> Optional[date]:
    """
    Parse date string according to format.

    Args:
        date_string: Date string to parse.
        format_string: Expected format pattern.

    Returns:
        Parsed date or None if invalid.
    """
    if not date_string:
        return None

    try:
        if format_string == 'YYYY-MM-DD':
            parts = date_string.split('-')
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        else:
            parts = date_string.split('.')
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except (ValueError, IndexError):
        return None


def get_week_range(d: date) -> tuple:
    """
    Get start and end date of week containing given date.

    Week starts on Monday.

    Args:
        d: Date within the week.

    Returns:
        Tuple of (monday, sunday) dates.
    """
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_month_range(year: int, month: int) -> tuple:
    """
    Get first and last date of a month.

    Args:
        year: Year number.
        month: Month number (1-12).

    Returns:
        Tuple of (first_day, last_day) dates.
    """
    first_day = date(year, month, 1)

    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    return first_day, last_day
