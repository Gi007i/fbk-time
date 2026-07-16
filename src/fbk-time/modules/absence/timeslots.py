"""Slot algebra for half-day and custom-time absences.

Pure functions that classify a date into a time slot and decide whether two
slots overlap. No database access; used by the conflict detection layer.
"""

from datetime import date, time
from typing import Optional, Tuple, Union


# Type alias for slot representation
# 'all_day', 'morning', 'afternoon', or ('custom', start_time, end_time)
SlotType = Union[str, Tuple[str, time, time]]


def _get_slot_for_date(
    current: date,
    start_date: date,
    end_date: date,
    is_all_day: bool,
    is_half_day_morning: bool,
    is_half_day_afternoon: bool,
    start_time: Optional[time],
    end_time: Optional[time]
) -> SlotType:
    """Determine the slot type for a specific date within an absence.

    For single-day absences, flags apply directly.
    For multi-day absences, half-day flags apply to boundary days only.

    Args:
        current: The date to check.
        start_date: Absence start date.
        end_date: Absence end date.
        is_all_day: All-day flag.
        is_half_day_morning: Morning half-day flag.
        is_half_day_afternoon: Afternoon half-day flag.
        start_time: Custom start time.
        end_time: Custom end time.

    Returns:
        Slot type: 'all_day', 'morning', 'afternoon', or ('custom', start, end).
    """
    is_single_day = start_date == end_date
    is_first_day = current == start_date
    is_last_day = current == end_date

    if is_single_day:
        if is_half_day_morning:
            return 'morning'
        if is_half_day_afternoon:
            return 'afternoon'
        if not is_all_day and start_time and end_time:
            return ('custom', start_time, end_time)
        return 'all_day'

    # Multi-day absence: half-day flags affect boundary days
    # is_half_day_morning on multi-day: first day afternoon only
    # is_half_day_afternoon on multi-day: last day morning only
    if is_first_day and is_half_day_morning:
        return 'afternoon'
    if is_last_day and is_half_day_afternoon:
        return 'morning'

    return 'all_day'


def _get_slot_for_occurrence(occurrence: dict) -> SlotType:
    """Determine the slot type for an expanded occurrence.

    For recurring absences, each occurrence is a single day.
    For non-recurring, uses start_date/end_date from the dict.

    Args:
        occurrence: Dict with is_all_day, is_half_day_morning, etc.

    Returns:
        Slot type: 'all_day', 'morning', 'afternoon', or ('custom', start, end).
    """
    start_date = occurrence.get('start_date', occurrence['date'])
    end_date = occurrence.get('end_date', occurrence['date'])
    current = occurrence['date']

    return _get_slot_for_date(
        current, start_date, end_date,
        occurrence['is_all_day'],
        occurrence['is_half_day_morning'],
        occurrence['is_half_day_afternoon'],
        occurrence.get('start_time'),
        occurrence.get('end_time')
    )


def _check_slot_conflict(
    new_slot: SlotType,
    existing_slot: SlotType
) -> Optional[str]:
    """Check if two time slots conflict.

    Args:
        new_slot: New absence slot type.
        existing_slot: Existing absence slot type.

    Returns:
        Conflict message if slots conflict, None otherwise.
    """
    new_type, new_times = _normalize_slot(new_slot)
    existing_type, existing_times = _normalize_slot(existing_slot)

    if existing_type == 'all_day':
        return 'Ganztägige Abwesenheit existiert bereits'
    if new_type == 'all_day':
        return 'Zeitslot bereits belegt'

    if new_type == existing_type:
        if new_type == 'morning':
            return 'Vormittag bereits belegt'
        if new_type == 'afternoon':
            return 'Nachmittag bereits belegt'

    if new_type == 'custom' or existing_type == 'custom':
        new_start, new_end = new_times if new_times else _half_day_times(new_type)
        ex_start, ex_end = existing_times if existing_times else _half_day_times(existing_type)
        if _times_overlap(new_start, new_end, ex_start, ex_end):
            return 'Zeiträume überlappen sich'

    return None


def occurrence_slot(occurrence: dict) -> SlotType:
    """Return the time slot for an expanded occurrence dict.

    Public wrapper around the slot classification for callers outside the
    conflict layer (e.g. the dashboard warning aggregation).
    """
    return _get_slot_for_occurrence(occurrence)


def slots_overlap(slot_a: SlotType, slot_b: SlotType) -> bool:
    """Return True when two slots occupy overlapping time on a day.

    Morning and afternoon do not overlap; any slot overlaps an all-day
    slot; custom time ranges are compared as real intervals.
    """
    return _check_slot_conflict(slot_a, slot_b) is not None


def _normalize_slot(slot: SlotType) -> Tuple[str, Optional[Tuple[time, time]]]:
    """Normalize slot to (type, times) tuple.

    Args:
        slot: Slot type string or custom time tuple.

    Returns:
        Tuple of (slot_type, optional_times).
    """
    if isinstance(slot, tuple):
        return ('custom', (slot[1], slot[2]))
    return (slot, None)


def _half_day_times(slot_type: str) -> Tuple[time, time]:
    """Get time boundaries for half-day slots.

    Args:
        slot_type: 'morning' or 'afternoon'.

    Returns:
        Tuple of (start_time, end_time).
    """
    if slot_type == 'morning':
        return (time(0, 0), time(12, 0))
    return (time(12, 0), time.max)


def _times_overlap(
    start1: time,
    end1: time,
    start2: time,
    end2: time
) -> bool:
    """Check if two time ranges overlap.

    Args:
        start1: Start time of first range.
        end1: End time of first range.
        start2: Start time of second range.
        end2: End time of second range.

    Returns:
        True if ranges overlap.
    """
    return start1 < end2 and end1 > start2
