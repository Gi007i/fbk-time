"""Request validation utilities.

Provides fail-fast validators for URL parameters with explicit error handling.
No silent fallbacks - invalid input results in 400 errors.
"""

from datetime import date, datetime
from typing import Optional

from flask import request, abort


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

    if value_str is None:
        if required:
            abort(400, f'Missing required parameter: {name}')
        return default

    if len(value_str) != 10:
        abort(400, 'Invalid date format')

    try:
        value = datetime.strptime(value_str, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'Invalid date format')

    current_year = date.today().year
    if value.year < current_year - 50 or value.year > current_year + 50:
        abort(400, 'Invalid date range')

    return value


def validate_year_param(default: Optional[int] = None) -> int:
    """Validate year URL parameter.

    Args:
        default: Default value (defaults to current year if None).

    Returns:
        Validated year within ±50 years of current year.

    Raises:
        abort(400) on invalid year.
    """
    current_year = date.today().year

    if default is None:
        default = current_year

    year_str = request.args.get('year')

    if year_str is None:
        return default

    try:
        year = int(year_str)
    except ValueError:
        abort(400, 'Invalid year')

    if year < current_year - 50 or year > current_year + 50:
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

    if month_str is None:
        return default

    try:
        month = int(month_str)
    except ValueError:
        abort(400, 'Invalid month')

    if month < 1 or month > 12:
        abort(400, 'Invalid month')

    return month


def validate_json_int(
    data: dict,
    name: str,
    required: bool = False,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None
) -> Optional[int]:
    """Validate integer from JSON request body.

    Args:
        data: Parsed JSON data dict.
        name: Key name in data.
        required: If True, return None signals error to caller.
        min_value: Minimum allowed value.
        max_value: Maximum allowed value.

    Returns:
        Validated integer or None if missing/invalid.
    """
    value = data.get(name)

    if value is None:
        return None

    try:
        value = int(value)
    except (ValueError, TypeError):
        return None

    if min_value is not None and value < min_value:
        return None

    if max_value is not None and value > max_value:
        return None

    return value


def validate_json_date(
    data: dict,
    name: str
) -> Optional[date]:
    """Validate date from JSON request body (YYYY-MM-DD format).

    Args:
        data: Parsed JSON data dict.
        name: Key name in data.

    Returns:
        Validated date or None if missing/invalid.
    """
    value_str = data.get(name)

    if not value_str or len(value_str) != 10:
        return None

    try:
        value = datetime.strptime(value_str, '%Y-%m-%d').date()
    except ValueError:
        return None

    current_year = date.today().year
    if value.year < current_year - 50 or value.year > current_year + 50:
        return None

    return value


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

    current_year = date.today().year
    if value.year < current_year - 50 or value.year > current_year + 50:
        return None

    return value


def validate_date_string(value_str: str) -> date:
    """Validate date string from URL path parameter (YYYY-MM-DD format).

    For use with Flask route parameters like <date_str>.
    Raises abort(400) on invalid format.

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

    current_year = date.today().year
    if value.year < current_year - 50 or value.year > current_year + 50:
        abort(400, 'Invalid date range')

    return value
