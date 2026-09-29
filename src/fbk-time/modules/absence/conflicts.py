"""Conflict detection for absence scheduling.

Expands recurring and non-recurring absences into per-day occurrences and
reports overlaps with existing entries and substitute assignments. Slot-level
overlap logic lives in ``timeslots``.
"""

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from core.db import db
from modules.absence.models import Absence, RecurrenceException
from modules.auth.models import User
from utils.helpers import format_date_for_user

from .recurrence import recurrence_service
from .timeslots import (
    _check_slot_conflict,
    _get_slot_for_occurrence,
    _slot_from_flags,
)


class ConflictResult:
    """Result of a conflict check.

    Conflicts are reported as non-blocking warnings via ``messages``.
    Callers surface them to the user but still allow the save to proceed.
    """

    def __init__(self):
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

    is_recurring = bool(rrule_str)
    range_end = (recurrence_end_date or end_date) if is_recurring else end_date

    new_dates = list(_expand_new_entry_dates(
        start_date, end_date, rrule_str, recurrence_end_date
    ))

    conflicting_dates = _get_user_conflict_dates_with_slots(
        user_id, start_date, range_end, new_dates,
        exclude_absence_id, time_flags
    )

    if conflicting_dates:
        result.add_warning(
            f'Überschneidung mit bestehender Abwesenheit an: '
            f'{_format_conflict_dates(conflicting_dates)}'
        )

    if substitute_id:
        new_slots = _get_new_entry_slots(new_dates, time_flags)

        substitute_absent_slots = get_user_absent_slots(
            substitute_id, start_date, range_end, exclude_absence_id
        )
        substitute_conflicts = _dates_with_slot_overlap(
            new_slots, substitute_absent_slots
        )
        if substitute_conflicts:
            result.add_warning(
                f'Vertretung {_get_user_name(substitute_id)} ist selbst '
                f'abwesend an: {_format_conflict_dates(substitute_conflicts)}'
            )

        existing_assignments = _get_substitute_assignment_dates(
            substitute_id, start_date, range_end, exclude_absence_id
        )
        assignment_conflicts = _dates_with_slot_overlap(
            new_slots, _group_assignment_slots(existing_assignments)
        )
        if assignment_conflicts:
            result.add_warning(
                f'{_get_user_name(substitute_id)} vertritt bereits andere '
                f'Personen an: {_format_conflict_dates(assignment_conflicts)}'
            )

        cross_assignments = _get_substitute_assignment_dates(
            user_id, start_date, range_end, exclude_absence_id
        )
        cross_slots = _group_assignment_slots(
            a for a in cross_assignments if a['user_id'] == substitute_id
        )
        cross_conflicts = _dates_with_slot_overlap(new_slots, cross_slots)
        if cross_conflicts:
            result.add_warning(
                'Kreuzvertretung erkannt: Die Personen vertreten sich gegenseitig '
                'im gleichen Zeitraum'
            )

    return result


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
    is_recurring = bool(rrule_str)
    range_end = (recurrence_end_date or end_date) if is_recurring else end_date

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
    new_slots = _get_new_entry_slots(new_dates, {
        'is_all_day': is_all_day,
        'is_half_day_morning': is_half_day_morning,
        'is_half_day_afternoon': is_half_day_afternoon,
        'start_time': start_time,
        'end_time': end_time
    })

    for new_date in new_dates:
        if new_date not in occurrences_by_date:
            continue

        for existing in occurrences_by_date[new_date]:
            conflict_msg = _check_slot_conflict(
                new_slots[new_date], _get_slot_for_occurrence(existing)
            )
            if conflict_msg:
                return False, (
                    f'Zeitkonflikt am {format_date_for_user(new_date)}: '
                    f'{conflict_msg}'
                )

    return True, None


def _get_user_name(user_id: int) -> str:
    """Get user name by ID."""
    user = db.session.get(User, user_id)
    return user.name if user else f'Person {user_id}'


def _format_conflict_dates(dates) -> str:
    """Format conflict dates as a short German phrase.

    Lists up to three dates; beyond that names the first two and a count
    of the remainder to keep warnings readable.
    """
    sorted_dates = sorted(dates)
    if len(sorted_dates) <= 3:
        return ', '.join(format_date_for_user(d) for d in sorted_dates)
    return (
        f'{format_date_for_user(sorted_dates[0])}, '
        f'{format_date_for_user(sorted_dates[1])} '
        f'und {len(sorted_dates) - 2} weitere Tage'
    )


def _get_new_entry_slots(
    new_dates: List[date],
    time_flags: Optional[dict]
) -> dict:
    """Map each date of the new entry to its time slot."""
    if not time_flags:
        time_flags = {'is_all_day': True}

    slot = _slot_from_flags(
        time_flags.get('is_all_day', True),
        time_flags.get('is_half_day_morning', False),
        time_flags.get('is_half_day_afternoon', False),
        time_flags.get('start_time'),
        time_flags.get('end_time')
    )
    return {occ_date: slot for occ_date in new_dates}


def _dates_with_slot_overlap(
    new_slots: dict,
    other_slots_by_date: dict
) -> set:
    """Return the new-entry dates whose slot overlaps another slot.

    A morning-only entry does not conflict with an afternoon-only slot on
    the same day.
    """
    conflicting = set()
    for occ_date, new_slot in new_slots.items():
        for other_slot in other_slots_by_date.get(occ_date, ()):
            if _check_slot_conflict(new_slot, other_slot):
                conflicting.add(occ_date)
                break
    return conflicting


def _group_assignment_slots(assignments) -> dict:
    """Group substitute-assignment slots by date."""
    slots_by_date = {}
    for assignment in assignments:
        slots_by_date.setdefault(assignment['date'], []).append(
            assignment['slot']
        )
    return slots_by_date


def get_user_absent_slots(
    user_id: int,
    range_start: date,
    range_end: date,
    exclude_absence_id: Optional[int] = None
) -> dict:
    """Map each date where the user is genuinely absent to its slots.

    Categories with is_present=True (e.g. remote work) are excluded:
    being present counts as available, not absent.

    Args:
        user_id: User ID.
        range_start: Start of date range.
        range_end: End of date range.
        exclude_absence_id: Absence ID to exclude.

    Returns:
        Dict mapping each absent date to a list of its time slots.
    """
    occurrences = _get_expanded_user_occurrences(
        user_id, range_start, range_end, exclude_absence_id,
        only_absent=True
    )
    slots_by_date = {}
    for occ in occurrences:
        slots_by_date.setdefault(occ['date'], []).append(
            _get_slot_for_occurrence(occ)
        )
    return slots_by_date


def _get_user_conflict_dates_with_slots(
    user_id: int,
    start_date: date,
    range_end: date,
    new_dates: List[date],
    exclude_absence_id: Optional[int],
    time_flags: Optional[dict]
) -> set:
    """Get dates with actual time slot conflicts for user.

    Checks each new date against existing absences considering time slots.
    Half-day morning + half-day afternoon on same date is NOT a conflict.

    Args:
        user_id: User ID.
        start_date: Start date of new absence.
        range_end: End of range to load existing occurrences for
            (recurrence_end_date for series, end_date otherwise).
        new_dates: List of dates for new absence.
        exclude_absence_id: Absence ID to exclude (for edits).
        time_flags: Time slot info (is_all_day, is_half_day_morning, etc.).

    Returns:
        Set of dates with actual time slot conflicts.
    """
    existing_occurrences = _get_expanded_user_occurrences(
        user_id, start_date, range_end, exclude_absence_id
    )

    if not existing_occurrences:
        return set()

    existing_slots_by_date = {}
    for occ in existing_occurrences:
        existing_slots_by_date.setdefault(occ['date'], []).append(
            _get_slot_for_occurrence(occ)
        )

    new_slots = _get_new_entry_slots(new_dates, time_flags)
    return _dates_with_slot_overlap(new_slots, existing_slots_by_date)


def _get_substitute_assignment_dates(
    substitute_id: int,
    range_start: date,
    range_end: date,
    exclude_absence_id: Optional[int] = None
) -> List[dict]:
    """Get all dates where user is assigned as substitute.

    Resolves recurring absences and their exceptions so that the
    reported assignments reflect the effective per-occurrence state:
    - Dates where the master substitute is ``substitute_id`` but a
      modified exception replaced it are excluded.
    - Dates where another series normally has a different substitute
      but a modified exception assigned ``substitute_id`` are included.

    Args:
        substitute_id: User ID of the substitute.
        range_start: Start of date range.
        range_end: End of date range.
        exclude_absence_id: Absence ID to exclude.

    Returns:
        List of dicts with date, slot, absence_id, user_id (the absent
        person). The slot reflects any per-occurrence exception override.
    """
    # An exception override can assign the substitute to a series whose
    # master substitute is someone else, so both sources are candidates.
    override_absence_ids = select(
        RecurrenceException.absence_id
    ).where(
        RecurrenceException.modified_substitute_overridden == True,
        RecurrenceException.modified_substitute_id == substitute_id
    )

    query = select(Absence).where(
        or_(
            Absence.substitute_id == substitute_id,
            Absence.id.in_(override_absence_ids)
        ),
        Absence.overlaps(range_start, range_end)
    ).options(selectinload(Absence.category))

    if exclude_absence_id:
        query = query.where(Absence.id != exclude_absence_id)

    absences = db.session.scalars(query).all()
    exceptions = recurrence_service.load_exceptions(
        absences, range_start, range_end
    )
    assignments = []

    for absence in absences:
        if absence.is_recurring and absence.rrule:
            # Deleted occurrences are already filtered; an exception
            # override wins over the master substitute.
            for occ_date, exception in recurrence_service.expand_occurrences(
                absence, range_start, range_end, exceptions[absence.id]
            ):
                if exception is not None and exception.modified_substitute_overridden:
                    effective_substitute_id = exception.modified_substitute_id
                else:
                    effective_substitute_id = absence.substitute_id

                if effective_substitute_id != substitute_id:
                    continue

                # A modified time type wins over the master's flags.
                if exception is not None and exception.modified_time_type is not None:
                    slot = exception.modified_time_type
                else:
                    slot = _get_slot_for_occurrence({
                        'date': occ_date,
                        'is_all_day': absence.is_all_day,
                        'is_half_day_morning': absence.is_half_day_morning,
                        'is_half_day_afternoon': absence.is_half_day_afternoon,
                        'start_time': absence.start_time,
                        'end_time': absence.end_time
                    })

                assignments.append({
                    'date': occ_date,
                    'slot': slot,
                    'absence_id': absence.id,
                    'user_id': absence.user_id
                })
        else:
            # Non-recurring absences cannot carry RecurrenceException
            # records, so the master substitute is always effective.
            if absence.substitute_id != substitute_id:
                continue
            if absence.start_date > range_end or absence.end_date < range_start:
                continue

            current = max(range_start, absence.start_date)
            end = min(range_end, absence.end_date)

            slot = _slot_from_flags(
                absence.is_all_day,
                absence.is_half_day_morning,
                absence.is_half_day_afternoon,
                absence.start_time, absence.end_time
            )
            while current <= end:
                assignments.append({
                    'date': current,
                    'slot': slot,
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
    query = select(Absence).where(
        Absence.user_id == user_id,
        Absence.overlaps(range_start, range_end)
    ).options(selectinload(Absence.category))

    if exclude_absence_id:
        query = query.where(Absence.id != exclude_absence_id)

    absences = db.session.scalars(query).all()
    exceptions = recurrence_service.load_exceptions(
        absences, range_start, range_end
    )
    occurrences = []

    for absence in absences:
        if absence.is_recurring and absence.rrule:
            for occ_date, exception in recurrence_service.expand_occurrences(
                absence, range_start, range_end, exceptions[absence.id]
            ):
                occ_data = recurrence_service.get_occurrence_data(
                    absence, occ_date, exception
                )
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

    Raises:
        ValueError: If the RRULE cannot be parsed.
    """
    if rrule_str:
        rule = recurrence_service.build_rule(start_date, rrule_str)

        dt_start = datetime.combine(start_date, datetime.min.time())
        effective_end = recurrence_end_date or end_date
        dt_end = datetime.combine(effective_end, datetime.max.time())

        return [dt.date() for dt in rule.between(dt_start, dt_end, inc=True)]

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates
