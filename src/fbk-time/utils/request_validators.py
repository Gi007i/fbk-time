"""Request validation utilities.

Provides fail-fast validators for URL parameters with explicit error handling.
No silent fallbacks - invalid input results in 400 errors.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from flask import request, abort


# Cap filter lists well below the SQLite host-parameter limit so an oversized
# request fails fast with 400 instead of a 500 raised by the IN(...) query.
_MAX_LIST_ITEMS = 200

# Accepted distance in years from today for any user-supplied date.
YEAR_RANGE = 50


def year_in_range(year: int) -> bool:
    """Return True when the year lies within YEAR_RANGE years of today."""
    current_year = date.today().year
    return current_year - YEAR_RANGE <= year <= current_year + YEAR_RANGE


def min_allowed_date() -> date:
    """Return the earliest date accepted from user input."""
    return date(date.today().year - YEAR_RANGE, 1, 1)


def validate_int_param(
    name: str,
    default: Optional[int] = None,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
    required: bool = False
) -> Optional[int]:
    """Validate integer URL parameter with fail-fast behavior.

    Args:
        name: Parameter name in request.args.
        default: Default value if parameter not provided (None = no default).
        min_value: Minimum allowed value (inclusive).
        max_value: Maximum allowed value (inclusive).
        required: If True, abort 400 when parameter missing.

    Returns:
        Validated integer value or default.

    Raises:
        abort(400) on invalid input or out-of-range value.
    """
    value_str = request.args.get(name)

    if value_str is None or value_str == '':
        if required:
            abort(400, f'Missing required parameter: {name}')
        return default

    try:
        value = int(value_str)
    except ValueError:
        abort(400, f'Invalid {name}')

    if min_value is not None and value < min_value:
        abort(400, f'Invalid {name}')

    if max_value is not None and value > max_value:
        abort(400, f'Invalid {name}')

    return value


def validate_int_list_param(
    name: str,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None
) -> list:
    """Validate a repeated integer URL parameter into a de-duplicated list.

    The empty "all" option is skipped; any non-integer or out-of-range value
    aborts 400 (no silent fallback). Example: ``?user_id=1&user_id=2``.

    Args:
        name: Parameter name in request.args.
        min_value: Minimum allowed value (inclusive).
        max_value: Maximum allowed value (inclusive).

    Returns:
        Validated ints in first-seen order; empty list means no filter.
    """
    values = request.args.getlist(name)
    if len(values) > _MAX_LIST_ITEMS:
        abort(400, f'Invalid {name}')

    result = []
    seen = set()
    for value_str in values:
        if value_str == '':
            continue
        try:
            value = int(value_str)
        except ValueError:
            abort(400, f'Invalid {name}')
        if min_value is not None and value < min_value:
            abort(400, f'Invalid {name}')
        if max_value is not None and value > max_value:
            abort(400, f'Invalid {name}')
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def validate_date_param(
    name: str,
    default: Optional[date] = None,
    required: bool = False
) -> Optional[date]:
    """Validate date URL parameter (YYYY-MM-DD format).

    Args:
        name: Parameter name in request.args.
        default: Default value if parameter not provided.
        required: If True, abort 400 when parameter missing.

    Returns:
        Validated date object or default.

    Raises:
        abort(400) on invalid format or out-of-range year.
    """
    value_str = request.args.get(name)

    if value_str is None or value_str == '':
        if required:
            abort(400, f'Missing required parameter: {name}')
        return default

    if len(value_str) != 10:
        abort(400, 'Invalid date format')

    try:
        value = datetime.strptime(value_str, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'Invalid date format')

    if not year_in_range(value.year):
        abort(400, 'Invalid date range')

    return value


def validate_year_param(default: Optional[int] = None) -> int:
    """Validate year URL parameter.

    Args:
        default: Default value (defaults to current year if None).

    Returns:
        Validated year within YEAR_RANGE years of the current year.

    Raises:
        abort(400) on invalid year.
    """
    if default is None:
        default = date.today().year

    year_str = request.args.get('year')

    if year_str is None or year_str == '':
        return default

    try:
        year = int(year_str)
    except ValueError:
        abort(400, 'Invalid year')

    if not year_in_range(year):
        abort(400, 'Invalid year')

    return year


def validate_month_param(default: Optional[int] = None) -> int:
    """Validate month URL parameter.

    Args:
        default: Default value (defaults to current month if None).

    Returns:
        Validated month (1-12).

    Raises:
        abort(400) on invalid month.
    """
    if default is None:
        default = date.today().month

    month_str = request.args.get('month')

    if month_str is None or month_str == '':
        return default

    try:
        month = int(month_str)
    except ValueError:
        abort(400, 'Invalid month')

    if month < 1 or month > 12:
        abort(400, 'Invalid month')

    return month


def parse_date_string(value_str: str) -> Optional[date]:
    """Parse date string without aborting (YYYY-MM-DD format).

    For use in bulk operations where invalid entries should be skipped.

    Args:
        value_str: Date string to parse.

    Returns:
        Validated date object or None if invalid.
    """
    if not value_str or len(value_str) != 10:
        return None

    try:
        value = datetime.strptime(value_str, '%Y-%m-%d').date()
    except ValueError:
        return None

    if not year_in_range(value.year):
        return None

    return value


def validate_date_string(value_str: str) -> date:
    """Validate date string from URL path parameter (YYYY-MM-DD format).

    Args:
        value_str: Date string to validate.

    Returns:
        Validated date object.

    Raises:
        abort(400) on invalid format or out-of-range year.
    """
    if len(value_str) != 10:
        abort(400, 'Invalid date format')

    try:
        value = datetime.strptime(value_str, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'Invalid date format')

    if not year_in_range(value.year):
        abort(400, 'Invalid date range')

    return value


def resolve_week_start(year: int, month: int) -> date:
    """Return the Monday of the requested week for a month view.

    Without a week_start parameter the week falls back to the current week
    when today lies in the shown month, otherwise to the week of the month's
    first day, so month navigation always stays within MAX_DATE_RANGE_DAYS.

    Args:
        year: Year of the shown month.
        month: Month of the shown month.

    Returns:
        Monday of the resolved week.
    """
    week_start = validate_date_param('week_start')
    if week_start is None:
        today = date.today()
        if (today.year, today.month) == (year, month):
            week_start = today
        else:
            week_start = date(year, month, 1)
    return week_start - timedelta(days=week_start.weekday())


MAX_DATE_RANGE_DAYS = 1830  # ~5 years


def validate_date_range(start: date, end: date) -> None:
    """Reject an inverted or unreasonably long date range (Fail-Fast).

    Raises:
        abort(400) when end precedes start or the span exceeds
        MAX_DATE_RANGE_DAYS.
    """
    if end < start:
        abort(400, 'Invalid date range: end before start')
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        abort(400, 'Invalid date range: span too large')
