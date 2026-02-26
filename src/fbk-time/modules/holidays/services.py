"""Holiday service.

Provides German holiday data using the holidays library (offline-capable).
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import holidays

from flask_login import current_user


GERMAN_STATES = {
    'none': ('Keine Feiertage', None),
    'DE-nationwide': ('Nur Bundesweit', None),
    'DE-all': ('Bundesweit + Alle Bundesländer', 'ALL'),
    'DE-BW': ('Baden-Württemberg', 'BW'),
    'DE-BY': ('Bayern', 'BY'),
    'DE-BE': ('Berlin', 'BE'),
    'DE-BB': ('Brandenburg', 'BB'),
    'DE-HB': ('Bremen', 'HB'),
    'DE-HH': ('Hamburg', 'HH'),
    'DE-HE': ('Hessen', 'HE'),
    'DE-MV': ('Mecklenburg-Vorpommern', 'MV'),
    'DE-NI': ('Niedersachsen', 'NI'),
    'DE-NW': ('Nordrhein-Westfalen', 'NW'),
    'DE-RP': ('Rheinland-Pfalz', 'RP'),
    'DE-SL': ('Saarland', 'SL'),
    'DE-SN': ('Sachsen', 'SN'),
    'DE-ST': ('Sachsen-Anhalt', 'ST'),
    'DE-SH': ('Schleswig-Holstein', 'SH'),
    'DE-TH': ('Thüringen', 'TH'),
}

ALL_STATE_CODES = ['BW', 'BY', 'BE', 'BB', 'HB', 'HH', 'HE', 'MV', 'NI', 'NW', 'RP', 'SL', 'SN', 'ST', 'SH', 'TH']


def get_current_region() -> str:
    """Get currently configured holiday region from user settings."""
    if current_user and current_user.is_authenticated:
        return current_user.holiday_region
    return 'none'


def get_holidays_for_year(year: int, region: Optional[str] = None) -> Dict[date, str]:
    """Get all holidays for a specific year and region.

    Args:
        year: The year to get holidays for.
        region: Region code (e.g., 'DE-nationwide', 'DE-BY'). Uses settings if None.

    Returns:
        Dictionary mapping dates to holiday names.
    """
    if region is None:
        region = get_current_region()

    if region == 'none':
        return {}

    if region == 'DE-all':
        result = {}
        nationwide = holidays.Germany(years=year)
        for d, name in nationwide.items():
            result[d] = name
        for state_code in ALL_STATE_CODES:
            state_holidays = holidays.Germany(years=year, prov=state_code)
            for d, name in state_holidays.items():
                if d not in result:
                    result[d] = name
        return result

    state_code = _get_state_code(region)

    if state_code:
        holiday_obj = holidays.Germany(years=year, prov=state_code)
    else:
        holiday_obj = holidays.Germany(years=year)

    return {d: name for d, name in holiday_obj.items()}


def get_holidays_for_range(
    start_date: date,
    end_date: date,
    region: Optional[str] = None
) -> Dict[date, str]:
    """Get all holidays within a date range.

    Args:
        start_date: Start of the range.
        end_date: End of the range.
        region: Region code. Uses settings if None.

    Returns:
        Dictionary mapping dates to holiday names.
    """
    if region is None:
        region = get_current_region()

    if region == 'none':
        return {}

    years = range(start_date.year, end_date.year + 1)

    result = {}
    for year in years:
        year_holidays = get_holidays_for_year(year, region)
        for d, name in year_holidays.items():
            if start_date <= d <= end_date:
                result[d] = name

    return result


def get_holidays_for_month(
    year: int,
    month: int,
    region: Optional[str] = None
) -> Dict[date, str]:
    """Get all holidays for a specific month.

    Args:
        year: The year.
        month: The month (1-12).
        region: Region code. Uses settings if None.

    Returns:
        Dictionary mapping dates to holiday names.
    """
    from calendar import monthrange

    start_date = date(year, month, 1)
    _, last_day = monthrange(year, month)
    end_date = date(year, month, last_day)

    return get_holidays_for_range(start_date, end_date, region)


def is_holiday(check_date: date, region: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Check if a specific date is a holiday.

    Args:
        check_date: The date to check.
        region: Region code. Uses settings if None.

    Returns:
        Tuple of (is_holiday, holiday_name).
    """
    if region is None:
        region = get_current_region()

    if region == 'none':
        return False, None

    holidays_dict = get_holidays_for_year(check_date.year, region)

    if check_date in holidays_dict:
        return True, holidays_dict[check_date]

    return False, None


def count_working_days(
    start_date: date,
    end_date: date,
    region: Optional[str] = None
) -> int:
    """Count working days in a date range (excludes weekends and holidays).

    Args:
        start_date: Start of the range.
        end_date: End of the range.
        region: Region code. Uses settings if None.

    Returns:
        Number of working days.
    """
    if start_date > end_date:
        return 0

    holidays_dict = get_holidays_for_range(start_date, end_date, region)
    count = 0

    current = start_date
    while current <= end_date:
        # Monday=0, Sunday=6
        if current.weekday() < 5:
            if current not in holidays_dict:
                count += 1
        current = current + timedelta(days=1)

    return count


def get_region_choices() -> List[Tuple[str, str]]:
    """Get list of region choices for form select field.

    Returns:
        List of (code, display_name) tuples.
    """
    return [(code, info[0]) for code, info in GERMAN_STATES.items()]


def _get_state_code(region: str) -> Optional[str]:
    """Get the state code for the holidays library.

    Args:
        region: Region code (e.g., 'DE-BY').

    Returns:
        State code for holidays library or None for nationwide.
    """
    if region in ('none', 'DE-nationwide', 'DE-all'):
        return None

    if region in GERMAN_STATES:
        return GERMAN_STATES[region][1]

    if region.startswith('DE-') and len(region) == 5:
        return region[3:]

    return None
