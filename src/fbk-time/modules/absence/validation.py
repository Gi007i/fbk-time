"""Validation service for absence management.

Provides conflict detection and validation for absences.
"""

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple, Union

from core.extensions import db
from modules.absence.models import Absence
from modules.auth.models import User
from modules.category.models import Category
from utils.helpers import format_date_for_user

from .recurrence import recurrence_service


# Type alias for slot representation
# 'all_day', 'morning', 'afternoon', or ('custom', start_time, end_time)
SlotType = Union[str, Tuple[str, time, time]]


class ConflictResult:
    """Result of a conflict check."""

    def __init__(self, has_conflicts: bool = False):
        self.has_conflicts = has_conflicts
        self.cross_substitution_warning: bool = False
        self.messages: List[str] = []

    def add_warning(self, message: str):
        """Add a warning message (not blocking)."""
        self.messages.append(message)


def check_absence_conflicts(
    user_id: int,
    start_date: date,
    end_date: date,
    exclude_absence_id: Optional[int] = None,
    substitute_id: Optional[int] = None,
    rrule_str: Optional[str] = None,
    recurrence_end_date: Optional[date] = None,
    time_flags: Optional[dict] = None
) -> ConflictResult:
    """Check for conflicts with existing absences.

    Expands recurring absences to check all occurrences for conflicts.
    Uses time slot information to avoid false positives for half-day combinations.

    Args:
        user_id: User for whom the absence is being created.
        start_date: Start date of the new absence.
        end_date: End date of the new absence.
        exclude_absence_id: ID of absence to exclude (for edits).
        substitute_id: ID of proposed substitute user.
        rrule_str: RRULE string for new recurring absence.
        recurrence_end_date: End date for recurring absence series.
        time_flags: Dict with is_all_day, is_half_day_morning,
            is_half_day_afternoon, start_time, end_time.

    Returns:
        ConflictResult with any found conflicts.
    """
    result = ConflictResult()

    is_recurring = rrule_str and recurrence_end_date
    range_end = recurrence_end_date if is_recurring else end_date

    new_dates = list(_expand_new_entry_dates(
        start_date, end_date, rrule_str, recurrence_end_date
    ))
    new_dates_set = set(new_dates)

    # Check user conflicts with time slot awareness
    conflicting_dates = _get_user_conflict_dates_with_slots(
        user_id, start_date, end_date, new_dates,
        exclude_absence_id, is_recurring, time_flags
    )

    if conflicting_dates:
        sorted_dates = sorted(conflicting_dates)
        result.has_conflicts = True
        if len(sorted_dates) <= 3:
            dates_str = ', '.join(format_date_for_user(d) for d in sorted_dates)
        else:
            dates_str = (
                f'{format_date_for_user(sorted_dates[0])}, '
                f'{format_date_for_user(sorted_dates[1])} '
                f'und {len(sorted_dates) - 2} weitere Tage'
            )
        result.messages.append(
            f'Überschneidung mit bestehender Abwesenheit an: {dates_str}'
        )

    if substitute_id:
        substitute_absence_dates = _get_user_absence_dates(
            substitute_id, start_date, range_end, exclude_absence_id
        )
        substitute_conflicts = new_dates_set & substitute_absence_dates

        if substitute_conflicts:
            sorted_dates = sorted(substitute_conflicts)
            result.has_conflicts = True
            if len(sorted_dates) <= 3:
                dates_str = ', '.join(format_date_for_user(d) for d in sorted_dates)
            else:
                dates_str = (
                    f'{format_date_for_user(sorted_dates[0])}, '
                    f'{format_date_for_user(sorted_dates[1])} '
                    f'und {len(sorted_dates) - 2} weitere Tage'
                )
            result.messages.append(
                f'Vertretung {_get_user_name(substitute_id)} ist selbst abwesend an: '
                f'{dates_str}'
            )

        existing_assignments = _get_substitute_assignment_dates(
            substitute_id, start_date, range_end, exclude_absence_id
        )
        assignment_dates = {a['date'] for a in existing_assignments}
        assignment_conflicts = new_dates_set & assignment_dates

        if assignment_conflicts:
            sorted_dates = sorted(assignment_conflicts)
            if len(sorted_dates) <= 3:
                dates_str = ', '.join(format_date_for_user(d) for d in sorted_dates)
            else:
                dates_str = (
                    f'{format_date_for_user(sorted_dates[0])}, '
                    f'{format_date_for_user(sorted_dates[1])} '
                    f'und {len(sorted_dates) - 2} weitere Tage'
                )
            result.add_warning(
                f'{_get_user_name(substitute_id)} vertritt bereits andere Personen an: '
                f'{dates_str}'
            )

        cross_assignments = _get_substitute_assignment_dates(
            user_id, start_date, range_end, exclude_absence_id
        )
        cross_dates = {
            a['date'] for a in cross_assignments
            if a['user_id'] == substitute_id
        }
        cross_conflicts = new_dates_set & cross_dates

        if cross_conflicts:
            result.cross_substitution_warning = True
            result.add_warning(
                'Kreuzvertretung erkannt: Die Personen vertreten sich gegenseitig '
                'im gleichen Zeitraum'
            )

    return result


def validate_substitute_required(
    category_id: int,
    substitute_id: Optional[int]
) -> Tuple[bool, Optional[str]]:
    """Validate that substitute is provided when required by category.

    Args:
        category_id: ID of the absence category.
        substitute_id: ID of the substitute user.

    Returns:
        Tuple of (is_valid, error_message).
    """
    category = db.session.get(Category, category_id)
    if not category:
        return False, 'Ungültige Kategorie'

    if category.requires_substitute and not substitute_id:
        return False, f'Kategorie "{category.name}" erfordert eine Vertretung'

    return True, None


def validate_substitute_not_self(
    user_id: int,
    substitute_id: Optional[int]
) -> Tuple[bool, Optional[str]]:
    """Validate that a user is not their own substitute.

    Args:
        user_id: ID of the absent user.
        substitute_id: ID of the substitute user.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if substitute_id and user_id == substitute_id:
        return False, 'Eine Person kann nicht ihre eigene Vertretung sein'

    return True, None


def validate_date_range(
    start_date: date,
    end_date: date
) -> Tuple[bool, Optional[str]]:
    """Validate that date range is valid.

    Args:
        start_date: Start date.
        end_date: End date.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not start_date or not end_date:
        return False, 'Start- und Enddatum sind erforderlich'

    if end_date < start_date:
        return False, 'Enddatum darf nicht vor dem Startdatum liegen'

    return True, None


def _get_user_name(user_id: int) -> str:
    """Get user name by ID."""
    user = db.session.get(User, user_id)
    return user.name if user else f'Person {user_id}'


def _get_user_absence_dates(
    user_id: int,
    range_start: date,
    range_end: date,
    exclude_absence_id: Optional[int] = None
) -> set:
    """Get all dates where user is genuinely absent in the range.

    Excludes categories with is_present=True.

    Args:
        user_id: User ID.
        range_start: Start of date range.
        range_end: End of date range.
        exclude_absence_id: Absence ID to exclude.

    Returns:
        Set of dates where user is genuinely absent.
    """
    occurrences = _get_expanded_user_occurrences(
        user_id, range_start, range_end, exclude_absence_id,
        only_absent=True
    )
    return {occ['date'] for occ in occurrences}


def _get_user_conflict_dates_with_slots(
    user_id: int,
    start_date: date,
    end_date: date,
    new_dates: List[date],
    exclude_absence_id: Optional[int],
    is_recurring: bool,
    time_flags: Optional[dict]
) -> set:
    """Get dates with actual time slot conflicts for user.

    Checks each new date against existing absences considering time slots.
    Half-day morning + half-day afternoon on same date is NOT a conflict.

    Args:
        user_id: User ID.
        start_date: Start date of new absence.
        end_date: End date of new absence.
        new_dates: List of dates for new absence.
        exclude_absence_id: Absence ID to exclude (for edits).
        is_recurring: Whether new absence is recurring.
        time_flags: Time slot info (is_all_day, is_half_day_morning, etc.).

    Returns:
        Set of dates with actual time slot conflicts.
    """
    if not time_flags:
        time_flags = {'is_all_day': True}

    range_end = new_dates[-1] if new_dates else end_date
    existing_occurrences = _get_expanded_user_occurrences(
        user_id, start_date, range_end, exclude_absence_id
    )

    if not existing_occurrences:
        return set()

    occurrences_by_date = {}
    for occ in existing_occurrences:
        occ_date = occ['date']
        if occ_date not in occurrences_by_date:
            occurrences_by_date[occ_date] = []
        occurrences_by_date[occ_date].append(occ)

    conflicting_dates = set()

    for new_date in new_dates:
        if new_date not in occurrences_by_date:
            continue

        if is_recurring:
            new_slot = _get_slot_for_date(
                new_date, new_date, new_date,
                time_flags.get('is_all_day', True),
                time_flags.get('is_half_day_morning', False),
                time_flags.get('is_half_day_afternoon', False),
                time_flags.get('start_time'),
                time_flags.get('end_time')
            )
        else:
            new_slot = _get_slot_for_date(
                new_date, start_date, end_date,
                time_flags.get('is_all_day', True),
                time_flags.get('is_half_day_morning', False),
                time_flags.get('is_half_day_afternoon', False),
                time_flags.get('start_time'),
                time_flags.get('end_time')
            )

        for existing in occurrences_by_date[new_date]:
            existing_slot = _get_slot_for_occurrence(existing)
            if _check_slot_conflict(new_slot, existing_slot):
                conflicting_dates.add(new_date)
                break

    return conflicting_dates


def _get_substitute_assignment_dates(
    substitute_id: int,
    range_start: date,
    range_end: date,
    exclude_absence_id: Optional[int] = None
) -> List[dict]:
    """Get all dates where user is assigned as substitute.

    Expands recurring absences to find all assignment dates.

    Args:
        substitute_id: User ID of the substitute.
        range_start: Start of date range.
        range_end: End of date range.
        exclude_absence_id: Absence ID to exclude.

    Returns:
        List of dicts with date, absence_id, user_id (the absent person).
    """
    query = Absence.query.filter(Absence.substitute_id == substitute_id)

    if exclude_absence_id:
        query = query.filter(Absence.id != exclude_absence_id)

    absences = query.all()
    assignments = []

    for absence in absences:
        if absence.is_recurring and absence.rrule:
            for occ_date, exception in recurrence_service.expand_occurrences(
                absence, range_start, range_end
            ):
                occ_data = recurrence_service.get_occurrence_data(absence, occ_date)
                if occ_data:
                    assignments.append({
                        'date': occ_date,
                        'absence_id': absence.id,
                        'user_id': absence.user_id
                    })
        else:
            if absence.start_date > range_end or absence.end_date < range_start:
                continue

            current = max(range_start, absence.start_date)
            end = min(range_end, absence.end_date)

            while current <= end:
                assignments.append({
                    'date': current,
                    'absence_id': absence.id,
                    'user_id': absence.user_id
                })
                current += timedelta(days=1)

    return assignments


def _get_expanded_user_occurrences(
    user_id: int,
    range_start: date,
    range_end: date,
    exclude_absence_id: Optional[int] = None,
    only_absent: bool = False
) -> List[dict]:
    """Get all expanded occurrences for a user within a date range.

    Expands recurring absences and combines with non-recurring ones.

    Args:
        user_id: User ID to get occurrences for.
        range_start: Start of date range.
        range_end: End of date range.
        exclude_absence_id: Absence ID to exclude (for edits).
        only_absent: If True, skip categories with is_present=True.

    Returns:
        List of occurrence dicts with date and slot information.
    """
    query = Absence.query.filter(Absence.user_id == user_id)

    if exclude_absence_id:
        query = query.filter(Absence.id != exclude_absence_id)

    absences = query.all()
    occurrences = []

    for absence in absences:
        if absence.is_recurring and absence.rrule:
            for occ_date, exception in recurrence_service.expand_occurrences(
                absence, range_start, range_end
            ):
                occ_data = recurrence_service.get_occurrence_data(absence, occ_date)
                if occ_data:
                    if only_absent and occ_data['category'].is_present:
                        continue
                    occurrences.append({
                        'date': occ_date,
                        'absence_id': absence.id,
                        'is_all_day': occ_data['is_all_day'],
                        'is_half_day_morning': occ_data['is_half_day_morning'],
                        'is_half_day_afternoon': occ_data['is_half_day_afternoon'],
                        'start_time': occ_data['start_time'],
                        'end_time': occ_data['end_time']
                    })
        else:
            if absence.start_date > range_end or absence.end_date < range_start:
                continue
            if only_absent and absence.category.is_present:
                continue

            current = max(range_start, absence.start_date)
            end = min(range_end, absence.end_date)

            while current <= end:
                occurrences.append({
                    'date': current,
                    'absence_id': absence.id,
                    'is_all_day': absence.is_all_day,
                    'is_half_day_morning': absence.is_half_day_morning,
                    'is_half_day_afternoon': absence.is_half_day_afternoon,
                    'start_time': absence.start_time,
                    'end_time': absence.end_time,
                    'start_date': absence.start_date,
                    'end_date': absence.end_date
                })
                current += timedelta(days=1)

    return occurrences


def validate_time_slot_overlap(
    user_id: int,
    start_date: date,
    end_date: date,
    is_all_day: bool,
    is_half_day_morning: bool,
    is_half_day_afternoon: bool,
    start_time: Optional[time] = None,
    end_time: Optional[time] = None,
    exclude_absence_id: Optional[int] = None,
    rrule_str: Optional[str] = None,
    recurrence_end_date: Optional[date] = None
) -> Tuple[bool, Optional[str]]:
    """Validate that no conflicting time slots exist for the user.

    Expands both existing recurring absences and new recurring entries to check
    all occurrences for conflicts.

    Args:
        user_id: User for whom the absence is being created.
        start_date: Start date of the new absence.
        end_date: End date of the new absence.
        is_all_day: True if absence is all-day.
        is_half_day_morning: True if absence is morning only.
        is_half_day_afternoon: True if absence is afternoon only.
        start_time: Custom start time (if not all-day or half-day).
        end_time: Custom end time (if not all-day or half-day).
        exclude_absence_id: ID of absence to exclude (for edits).
        rrule_str: RRULE string for new recurring absence.
        recurrence_end_date: End date for recurring absence series.

    Returns:
        Tuple of (is_valid, error_message).
    """
    is_recurring = rrule_str and recurrence_end_date
    range_end = recurrence_end_date if is_recurring else end_date

    existing_occurrences = _get_expanded_user_occurrences(
        user_id, start_date, range_end, exclude_absence_id
    )

    if not existing_occurrences:
        return True, None

    occurrences_by_date = {}
    for occ in existing_occurrences:
        occ_date = occ['date']
        if occ_date not in occurrences_by_date:
            occurrences_by_date[occ_date] = []
        occurrences_by_date[occ_date].append(occ)

    new_dates = _expand_new_entry_dates(start_date, end_date, rrule_str, recurrence_end_date)

    for new_date in new_dates:
        if new_date not in occurrences_by_date:
            continue

        if is_recurring:
            new_slot = _get_slot_for_date(
                new_date, new_date, new_date,
                is_all_day, is_half_day_morning, is_half_day_afternoon,
                start_time, end_time
            )
        else:
            new_slot = _get_slot_for_date(
                new_date, start_date, end_date,
                is_all_day, is_half_day_morning, is_half_day_afternoon,
                start_time, end_time
            )

        for existing in occurrences_by_date[new_date]:
            existing_slot = _get_slot_for_occurrence(existing)
            conflict_msg = _check_slot_conflict(new_slot, existing_slot)
            if conflict_msg:
                return False, (
                    f'Zeitkonflikt am {format_date_for_user(new_date)}: '
                    f'{conflict_msg}'
                )

    return True, None


def _expand_new_entry_dates(
    start_date: date,
    end_date: date,
    rrule_str: Optional[str],
    recurrence_end_date: Optional[date]
) -> List[date]:
    """Expand dates for a new absence entry.

    For recurring entries, expands the RRULE. For non-recurring, returns
    all dates in the range.

    Args:
        start_date: Start date of the absence.
        end_date: End date (for non-recurring).
        rrule_str: RRULE string (for recurring).
        recurrence_end_date: Series end date (for recurring).

    Returns:
        List of dates covered by the new entry.
    """
    if rrule_str and recurrence_end_date:
        from dateutil.rrule import rrulestr

        dtstart = start_date.strftime('%Y%m%dT000000')
        rrule_full = f"DTSTART:{dtstart}\nRRULE:{rrule_str}"

        try:
            rule = rrulestr(rrule_full)
        except (ValueError, TypeError):
            return [start_date]

        dt_start = datetime.combine(start_date, datetime.min.time())
        dt_end = datetime.combine(recurrence_end_date, datetime.max.time())

        return [dt.date() for dt in rule.between(dt_start, dt_end, inc=True)]

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


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

    # All-day conflicts with everything
    if existing_type == 'all_day':
        return 'Ganztägige Abwesenheit existiert bereits'
    if new_type == 'all_day':
        return 'Zeitslot bereits belegt'

    # Same half-day type conflicts
    if new_type == existing_type:
        if new_type == 'morning':
            return 'Vormittag bereits belegt'
        if new_type == 'afternoon':
            return 'Nachmittag bereits belegt'

    # Custom time requires actual time overlap check
    if new_type == 'custom' or existing_type == 'custom':
        new_start, new_end = new_times if new_times else _half_day_times(new_type)
        ex_start, ex_end = existing_times if existing_times else _half_day_times(existing_type)
        if _times_overlap(new_start, new_end, ex_start, ex_end):
            return 'Zeiträume überlappen sich'

    return None


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
    return (time(12, 0), time(23, 59))


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
