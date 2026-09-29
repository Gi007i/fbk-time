"""Absence business logic: CRUD, validation, history and recurrence."""

from calendar import monthrange
from datetime import date
from typing import Literal, Optional, Tuple

from flask import current_app, abort
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from core.auth import current_user
from core.db import db
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from utils.helpers import format_date_for_user

from .models import Absence, AbsenceHistory, RecurrenceException
from .validation import (
    check_absence_conflicts,
    validate_substitute_required,
    validate_substitute_not_self,
    validate_category_assignable,
    validate_custom_time_span,
    validate_date_range,
    validate_time_slot_overlap,
    ConflictResult,
)
from .history import (
    create_initial_history,
    track_absence_changes,
    track_exception_pruned,
    track_occurrence_modifications,
    track_occurrence_deletion,
    track_occurrence_restoration
)
from .recurrence import recurrence_service


def get_absence_or_404(absence_id: int) -> Absence:
    """Get absence by ID or abort with 404.

    Args:
        absence_id: Absence ID.

    Returns:
        Absence instance.

    Raises:
        404: If absence not found.
    """
    absence = db.session.get(Absence, absence_id)
    if absence is None:
        abort(404)
    return absence


def get_exception_for_date(
    absence: Absence, exception_date: date
) -> Optional[RecurrenceException]:
    """Return the RecurrenceException for a given date, if any.

    Args:
        absence: Parent recurring absence.
        exception_date: Date to look up.

    Returns:
        Matching RecurrenceException or None.
    """
    return recurrence_service._get_exception(absence, exception_date)


def is_editable_occurrence(absence: Absence, occurrence_date: date) -> bool:
    """Report whether a date of a series can be opened for editing.

    An unreadable stored pattern counts as not editable; the reason is logged.

    Args:
        absence: Parent recurring absence.
        occurrence_date: Requested occurrence date.

    Returns:
        True if an exception exists for the date or it is a valid occurrence.
    """
    if get_exception_for_date(absence, occurrence_date) is not None:
        return True
    try:
        return recurrence_service.is_valid_occurrence_date(absence, occurrence_date)
    except ValueError:
        current_app.logger.error(
            'Absence %s: unreadable RRULE, occurrence not editable', absence.id
        )
        return False


def can_modify_absence(absence: Absence) -> bool:
    """Check if current user can modify an absence.

    Args:
        absence: Absence to check.

    Returns:
        True if user owns the absence or is Manager/Admin.
    """
    if absence.user_id == current_user.id:
        return True
    if current_user.role in (UserRole.ADMIN, UserRole.MANAGER):
        return True
    return False


def validate_absence_data(
    user_id: int,
    category_id: int,
    start_date: date,
    end_date: date,
    substitute_id: Optional[int],
    time_flags: Optional[dict] = None,
    exclude_absence_id: Optional[int] = None,
    recurrence_data: Optional[dict] = None,
    current_category_id: Optional[int] = None
) -> Tuple[bool, Optional[str], Optional[ConflictResult]]:
    """Validate absence data before create/update.

    Args:
        user_id: User the absence is for.
        category_id: Category ID.
        start_date: Start date.
        end_date: End date.
        substitute_id: Substitute user ID.
        time_flags: Dict with is_all_day, is_half_day_morning,
            is_half_day_afternoon, start_time, end_time.
        exclude_absence_id: Absence ID to exclude from conflict check.
        recurrence_data: Dict with is_recurring, rrule, recurrence_end_date.
        current_category_id: Category currently stored on the record, None
            on create; a disabled category may be kept but not newly assigned.

    Returns:
        Tuple of (is_valid, error_message, conflicts).
    """
    is_valid, error = validate_date_range(start_date, end_date)
    if not is_valid:
        return False, error, None

    is_valid, error = validate_category_assignable(
        category_id, current_category_id
    )
    if not is_valid:
        return False, error, None

    is_valid, error = validate_substitute_required(category_id, substitute_id)
    if not is_valid:
        return False, error, None

    is_valid, error = validate_substitute_not_self(user_id, substitute_id)
    if not is_valid:
        return False, error, None

    rrule_str = None
    recurrence_end = None
    is_recurring = bool(recurrence_data and recurrence_data.get('is_recurring'))
    if is_recurring:
        rrule_str = recurrence_data.get('rrule')
        recurrence_end = recurrence_data.get('recurrence_end_date')

    # A recurring absence is one day per occurrence, so its window is fine.
    if time_flags and not is_recurring:
        is_valid, error = validate_custom_time_span(
            start_date,
            end_date,
            time_flags.get('start_time'),
            time_flags.get('end_time')
        )
        if not is_valid:
            return False, error, None

    if time_flags:
        is_valid, error = validate_time_slot_overlap(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            is_all_day=time_flags.get('is_all_day', True),
            is_half_day_morning=time_flags.get('is_half_day_morning', False),
            is_half_day_afternoon=time_flags.get('is_half_day_afternoon', False),
            start_time=time_flags.get('start_time'),
            end_time=time_flags.get('end_time'),
            exclude_absence_id=exclude_absence_id,
            rrule_str=rrule_str,
            recurrence_end_date=recurrence_end
        )
        if not is_valid:
            return False, error, None

    conflicts = check_absence_conflicts(
        user_id,
        start_date,
        end_date,
        exclude_absence_id=exclude_absence_id,
        substitute_id=substitute_id,
        rrule_str=rrule_str,
        recurrence_end_date=recurrence_end,
        time_flags=time_flags
    )

    return True, None, conflicts


def create_absence(
    user_id: int,
    category_id: int,
    start_date: date,
    end_date: date,
    time_flags: dict,
    recurrence_data: dict,
    substitute_id: Optional[int] = None,
    notes: Optional[str] = None
) -> Tuple[Absence, str]:
    """Create a new absence record.

    Args:
        user_id: User the absence is for.
        category_id: Category ID.
        start_date: Start date.
        end_date: End date (adjusted for recurring).
        time_flags: Dict with is_all_day, is_half_day_morning, etc.
        recurrence_data: Dict with is_recurring, rrule, recurrence_end_date.
        substitute_id: Optional substitute user ID.
        notes: Optional notes.

    Returns:
        Tuple of (created Absence, success message).
    """
    absence = Absence(
        user_id=user_id,
        category_id=category_id,
        start_date=start_date,
        end_date=end_date if not recurrence_data['is_recurring'] else start_date,
        start_time=time_flags.get('start_time'),
        end_time=time_flags.get('end_time'),
        is_all_day=time_flags.get('is_all_day', True),
        is_half_day_morning=time_flags.get('is_half_day_morning', False),
        is_half_day_afternoon=time_flags.get('is_half_day_afternoon', False),
        substitute_id=substitute_id,
        notes=notes.strip() if notes else None,
        is_recurring=recurrence_data['is_recurring'],
        rrule=recurrence_data.get('rrule'),
        recurrence_end_date=recurrence_data.get('recurrence_end_date')
    )

    db.session.add(absence)
    db.session.flush()

    # TOCTOU: after the flush this session holds the SQLite write lock, so no
    # concurrent writer can commit between this re-check and the insert.
    recheck_valid, recheck_error = validate_time_slot_overlap(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date if not recurrence_data['is_recurring'] else start_date,
        is_all_day=time_flags.get('is_all_day', True),
        is_half_day_morning=time_flags.get('is_half_day_morning', False),
        is_half_day_afternoon=time_flags.get('is_half_day_afternoon', False),
        start_time=time_flags.get('start_time'),
        end_time=time_flags.get('end_time'),
        exclude_absence_id=absence.id,
        rrule_str=recurrence_data.get('rrule'),
        recurrence_end_date=recurrence_data.get('recurrence_end_date')
    )
    if not recheck_valid:
        db.session.rollback()
        raise ValueError(recheck_error)

    create_initial_history(absence)

    user = db.session.get(User, user_id)

    if absence.is_recurring:
        occurrence_count = recurrence_service.count_occurrences(absence)
        pattern_desc = recurrence_service.get_recurrence_description(
            absence.rrule, absence.recurrence_end_date
        )
        message = (
            f'Wiederkehrende Abwesenheit für "{user.name}" erstellt: '
            f'{pattern_desc} ({occurrence_count} Termine).'
        )
    else:
        message = (
            f'Abwesenheit für "{user.name}" vom '
            f'{format_date_for_user(absence.start_date)} bis '
            f'{format_date_for_user(absence.end_date)} wurde erstellt.'
        )

    return absence, message


def _prune_orphaned_exceptions(absence: Absence) -> None:
    """Remove exceptions whose date is no longer part of the series.

    The raw pattern is checked rather than the expanded occurrences, so
    deleted occurrences still in the series survive unrelated edits. Every
    removal is recorded in the history.

    Raises:
        ValueError: If the RRULE cannot be parsed; nothing is deleted then.
    """
    exceptions = db.session.scalars(absence.exceptions.select()).all()

    if not absence.is_recurring or not absence.rrule:
        orphaned = exceptions
    else:
        orphaned = [
            exc for exc in exceptions
            if not recurrence_service.is_date_in_rrule(absence, exc.exception_date)
        ]

    for exc in orphaned:
        track_exception_pruned(absence, exc)
        db.session.delete(exc)


def update_absence(
    absence: Absence,
    user_id: int,
    category_id: int,
    start_date: date,
    end_date: date,
    time_flags: dict,
    recurrence_data: dict,
    substitute_id: Optional[int] = None,
    notes: Optional[str] = None
) -> str:
    """Update an existing absence record.

    Args:
        absence: Absence to update.
        user_id: User the absence is for.
        category_id: Category ID.
        start_date: Start date.
        end_date: End date (adjusted for recurring).
        time_flags: Dict with is_all_day, is_half_day_morning, etc.
        recurrence_data: Dict with is_recurring, rrule, recurrence_end_date.
        substitute_id: Optional substitute user ID.
        notes: Optional notes.

    Returns:
        Success message.
    """
    form_data = {
        'user_id': user_id,
        'category_id': category_id,
        'start_date': start_date,
        'end_date': end_date if not recurrence_data['is_recurring'] else start_date,
        'start_time': time_flags.get('start_time'),
        'end_time': time_flags.get('end_time'),
        'is_all_day': time_flags.get('is_all_day', True),
        'is_half_day_morning': time_flags.get('is_half_day_morning', False),
        'is_half_day_afternoon': time_flags.get('is_half_day_afternoon', False),
        'substitute_id': substitute_id,
        'notes': notes.strip() if notes else None,
        'is_recurring': recurrence_data['is_recurring'],
        'rrule': recurrence_data.get('rrule'),
        'recurrence_end_date': recurrence_data.get('recurrence_end_date')
    }

    track_absence_changes(absence, form_data)

    absence.user_id = user_id
    absence.category_id = category_id
    absence.start_date = start_date
    absence.end_date = end_date if not recurrence_data['is_recurring'] else start_date
    absence.start_time = time_flags.get('start_time')
    absence.end_time = time_flags.get('end_time')
    absence.is_all_day = time_flags.get('is_all_day', True)
    absence.is_half_day_morning = time_flags.get('is_half_day_morning', False)
    absence.is_half_day_afternoon = time_flags.get('is_half_day_afternoon', False)
    absence.substitute_id = substitute_id
    absence.notes = notes.strip() if notes else None
    absence.is_recurring = recurrence_data['is_recurring']
    absence.rrule = recurrence_data.get('rrule')
    absence.recurrence_end_date = recurrence_data.get('recurrence_end_date')

    _prune_orphaned_exceptions(absence)

    db.session.flush()

    # Re-validate after flush to reduce TOCTOU race window
    recheck_valid, recheck_error = validate_time_slot_overlap(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date if not recurrence_data['is_recurring'] else start_date,
        is_all_day=time_flags.get('is_all_day', True),
        is_half_day_morning=time_flags.get('is_half_day_morning', False),
        is_half_day_afternoon=time_flags.get('is_half_day_afternoon', False),
        start_time=time_flags.get('start_time'),
        end_time=time_flags.get('end_time'),
        exclude_absence_id=absence.id,
        rrule_str=recurrence_data.get('rrule'),
        recurrence_end_date=recurrence_data.get('recurrence_end_date')
    )
    if not recheck_valid:
        db.session.rollback()
        # The in-memory object still carries the rejected mutations; refresh
        # so the caller renders committed state.
        try:
            db.session.refresh(absence)
        except SQLAlchemyError as refresh_error:
            current_app.logger.warning(
                'Failed to refresh absence %s after rollback: %s',
                absence.id, refresh_error
            )
        raise ValueError(recheck_error)

    return 'Abwesenheit wurde aktualisiert.'


def delete_absence(absence: Absence) -> str:
    """Delete an absence record.

    Args:
        absence: Absence to delete.

    Returns:
        Success message.
    """
    user_name = absence.user.name if absence.user else 'Unbekannt'

    if absence.is_recurring and absence.recurrence_end_date:
        date_range = (
            f'{format_date_for_user(absence.start_date)} - '
            f'{format_date_for_user(absence.recurrence_end_date)}'
        )
    else:
        date_range = (
            f'{format_date_for_user(absence.start_date)} - '
            f'{format_date_for_user(absence.end_date)}'
        )

    db.session.delete(absence)

    return f'Abwesenheit für "{user_name}" ({date_range}) wurde gelöscht.'


def modify_occurrence(
    absence: Absence,
    occurrence_date: date,
    effective_state: dict
) -> Tuple[str, list]:
    """Modify a single occurrence of a recurring absence.

    The merged state is validated like a normal absence; an absent
    substitute is a warning, as on create and edit. Only fields differing
    from the parent are stored as overrides.

    Args:
        absence: Parent recurring absence.
        occurrence_date: Date of occurrence to modify.
        effective_state: Complete desired state dict with keys
            'category_id', 'time_type', 'substitute_id', 'notes'.

    Returns:
        Tuple of (success message, non-blocking substitute warnings).

    Raises:
        ValueError: If occurrence_date is invalid, was previously
            deleted, effective_state is incomplete, or the resulting
            occurrence fails validation.
    """
    required_keys = {'category_id', 'time_type', 'substitute_id', 'notes'}
    missing = required_keys - set(effective_state.keys())
    if missing:
        raise ValueError(
            f'effective_state missing required keys: {sorted(missing)}'
        )

    effective_category_id = effective_state['category_id']
    effective_substitute_id = effective_state['substitute_id']
    time_type = effective_state['time_type']

    if time_type not in ('all_day', 'morning', 'afternoon', 'custom_time'):
        raise ValueError(f'Invalid time_type: {time_type!r}')
    if (
        time_type == 'custom_time'
        and recurrence_service.parent_time_type(absence) != 'custom_time'
    ):
        raise ValueError('Diese Serie hat keine eigene Uhrzeit.')
    time_flags = _occurrence_time_flags(absence, time_type)

    before_data = recurrence_service.get_occurrence_data(
        absence, occurrence_date
    ) or {}

    is_valid, error = validate_category_assignable(
        effective_category_id, before_data.get('category_id')
    )
    if not is_valid:
        raise ValueError(error)

    is_valid, error = validate_substitute_required(
        effective_category_id, effective_substitute_id
    )
    if not is_valid:
        raise ValueError(error)

    is_valid, error = validate_substitute_not_self(
        absence.user_id, effective_substitute_id
    )
    if not is_valid:
        raise ValueError(error)

    is_valid, error = validate_time_slot_overlap(
        user_id=absence.user_id,
        start_date=occurrence_date,
        end_date=occurrence_date,
        exclude_absence_id=absence.id,
        **time_flags
    )
    if not is_valid:
        raise ValueError(error)

    warnings = []
    if effective_substitute_id is not None:
        warnings = check_absence_conflicts(
            user_id=absence.user_id,
            start_date=occurrence_date,
            end_date=occurrence_date,
            exclude_absence_id=absence.id,
            substitute_id=effective_substitute_id,
            time_flags=time_flags
        ).messages

    effective_before = _effective_state_of(before_data)

    recurrence_service.modify_occurrence(
        absence, occurrence_date, effective_state
    )

    after_data = recurrence_service.get_occurrence_data(
        absence, occurrence_date
    ) or {}

    track_occurrence_modifications(
        absence, occurrence_date, effective_before, _effective_state_of(after_data)
    )

    return f'Termin am {format_date_for_user(occurrence_date)} wurde geändert.', warnings


def _occurrence_time_flags(absence: Absence, time_type: str) -> dict:
    """Map an occurrence time_type to slot flags; 'custom_time' keeps the series window."""
    is_custom = time_type == 'custom_time'
    return {
        'is_all_day': time_type == 'all_day',
        'is_half_day_morning': time_type == 'morning',
        'is_half_day_afternoon': time_type == 'afternoon',
        'start_time': absence.start_time if is_custom else None,
        'end_time': absence.end_time if is_custom else None,
    }


def _effective_state_of(occ_data: dict) -> dict:
    """Reduce merged occurrence data to the fields tracked in the history."""
    return {
        'category_id': occ_data.get('category_id'),
        'time_type': time_flags_to_type(occ_data),
        'substitute_id': occ_data.get('substitute_id'),
        'notes': occ_data.get('notes')
    }


def time_flags_to_type(
    occ_data: dict
) -> Literal['all_day', 'morning', 'afternoon', 'custom_time']:
    """Derive the time_type enum value from merged occurrence data."""
    if occ_data.get('is_half_day_morning'):
        return 'morning'
    if occ_data.get('is_half_day_afternoon'):
        return 'afternoon'
    if (
        not occ_data.get('is_all_day')
        and occ_data.get('start_time')
        and occ_data.get('end_time')
    ):
        return 'custom_time'
    return 'all_day'


def restore_occurrence(absence: Absence, occurrence_date: date) -> Tuple[str, list]:
    """Restore a deleted or modified occurrence to its series defaults.

    Validates that the restored occurrence does not conflict with
    existing absences before removing the exception. An absent
    substitute is a warning, as on create and edit.

    Args:
        absence: Parent recurring absence.
        occurrence_date: Date of occurrence to restore.

    Returns:
        Tuple of (success message, non-blocking substitute warnings).

    Raises:
        ValueError: If no exception exists or restoration would
            cause a conflict.
    """
    exception = recurrence_service._get_exception(absence, occurrence_date)
    if not exception:
        raise ValueError(
            f'Keine Ausnahme am {format_date_for_user(occurrence_date)} vorhanden.'
        )

    time_flags = {
        'is_all_day': absence.is_all_day,
        'is_half_day_morning': absence.is_half_day_morning,
        'is_half_day_afternoon': absence.is_half_day_afternoon,
        'start_time': absence.start_time,
        'end_time': absence.end_time,
    }
    is_valid, error = validate_time_slot_overlap(
        user_id=absence.user_id,
        start_date=occurrence_date,
        end_date=occurrence_date,
        exclude_absence_id=absence.id,
        **time_flags
    )
    if not is_valid:
        raise ValueError(error)

    warnings = []
    if absence.substitute_id is not None:
        warnings = check_absence_conflicts(
            user_id=absence.user_id,
            start_date=occurrence_date,
            end_date=occurrence_date,
            exclude_absence_id=absence.id,
            substitute_id=absence.substitute_id,
            time_flags=time_flags
        ).messages

    was_deleted = exception.exception_type == 'deleted'
    if was_deleted:
        db.session.delete(exception)
        track_occurrence_restoration(absence, occurrence_date)
        return f'Termin am {format_date_for_user(occurrence_date)} wurde wiederhergestellt.', warnings

    effective_before = _effective_state_of(
        recurrence_service.get_occurrence_data(absence, occurrence_date, exception)
    )
    db.session.delete(exception)
    effective_after = _effective_state_of(
        recurrence_service.get_occurrence_data(absence, occurrence_date, None)
    )
    track_occurrence_modifications(
        absence, occurrence_date, effective_before, effective_after
    )
    return f'Termin am {format_date_for_user(occurrence_date)} wurde auf Serienwerte zurückgesetzt.', warnings


def delete_occurrence(absence: Absence, occurrence_date: date) -> str:
    """Delete a single occurrence from a recurring absence.

    Args:
        absence: Parent recurring absence.
        occurrence_date: Date of occurrence to delete.

    Returns:
        Success message.

    Raises:
        ValueError: If occurrence_date is not a valid date in the series.
    """
    recurrence_service.delete_occurrence(absence, occurrence_date)
    track_occurrence_deletion(absence, occurrence_date)
    return f'Termin am {format_date_for_user(occurrence_date)} wurde aus der Serie entfernt.'


def get_active_users_for_form() -> list[User]:
    """Get list of users for absence form dropdowns.

    Returns:
        List of active/managed USER role users.
    """
    return db.session.scalars(
        select(User).where(
            User.role == UserRole.USER,
            User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
        ).order_by(User.name)
    ).all()


def get_active_categories() -> list[Category]:
    """Get list of active categories for forms.

    Returns:
        List of active categories ordered by sort_order.
    """
    return db.session.scalars(
        select(Category).filter_by(active=True).order_by(Category.sort_order)
    ).all()


def get_substitute_choices(exclude_user_id: Optional[int] = None) -> list[User]:
    """Get list of users eligible as substitutes.

    Args:
        exclude_user_id: User ID to exclude from list.

    Returns:
        List of users who can be substitutes.
    """
    query = select(User).where(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    )

    if exclude_user_id:
        query = query.where(User.id != exclude_user_id)

    return db.session.scalars(query.order_by(User.name)).all()


def get_absences_list(
    date_from: date,
    date_to: date,
    user_ids: Optional[list[int]] = None
) -> list[Absence]:
    """Get absences overlapping a date range for active users.

    Category and substitute filters apply after expansion, so modified
    occurrences are filtered by their effective state.

    Args:
        date_from: Start date of range.
        date_to: End date of range.
        user_ids: Optional person filter (any of the given IDs).

    Returns:
        List of absences whose master record overlaps the range.
    """
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = select(Absence).join(
        User, Absence.user_id == User.id
    ).where(
        user_status_filter,
        User.role == UserRole.USER,
        Absence.overlaps(date_from, date_to)
    ).options(
        selectinload(Absence.user),
        selectinload(Absence.category),
        selectinload(Absence.substitute)
    )

    if user_ids:
        query = query.where(Absence.user_id.in_(user_ids))

    return db.session.scalars(query).all()


def default_list_range() -> Tuple[date, date]:
    """Return the range the list view covers when no dates are given.

    Shared with the overview counts, so a linked count and the list it opens
    always describe the same period.

    Returns:
        Tuple of (first day, last day) of the current month.
    """
    first = date.today().replace(day=1)
    return first, first.replace(day=monthrange(first.year, first.month)[1])


def _expanded_occurrences_for_range(
    date_from: date,
    date_to: date,
    user_ids: Optional[list[int]] = None
) -> list[dict]:
    """Expand all absences overlapping a range into one entry per day."""
    absences = get_absences_list(date_from, date_to, user_ids)
    return recurrence_service.get_all_occurrences_for_range(
        absences, date_from, date_to
    )


def count_occurrences_by_user(
    date_from: date,
    date_to: date,
    user_ids: Optional[list[int]] = None
) -> dict:
    """Count occurrences per user for a date range.

    Args:
        date_from: Start date of range.
        date_to: End date of range.
        user_ids: Optional person filter.

    Returns:
        Dict mapping user ID to occurrence count.
    """
    counts = {}
    for occ in _expanded_occurrences_for_range(date_from, date_to, user_ids):
        counts[occ['user_id']] = counts.get(occ['user_id'], 0) + 1
    return counts


def count_occurrences_by_category(date_from: date, date_to: date) -> dict:
    """Count occurrences per effective category for a date range.

    Args:
        date_from: Start date of range.
        date_to: End date of range.

    Returns:
        Dict mapping category ID to occurrence count.
    """
    counts = {}
    for occ in _expanded_occurrences_for_range(date_from, date_to):
        counts[occ['category_id']] = counts.get(occ['category_id'], 0) + 1
    return counts


def filter_occurrences(
    occurrences: list[dict],
    category_ids: Optional[list[int]] = None,
    has_substitute: Optional[str] = None
) -> list[dict]:
    """Filter expanded occurrences by effective state.

    Args:
        occurrences: List of expanded occurrence dicts.
        category_ids: Optional category filter (any of the given IDs)
            applied to the effective category of each occurrence (after
            exception merge).
        has_substitute: 'yes', 'no', or None. Filters by effective
            substitute presence.

    Returns:
        Filtered list of occurrence dicts.
    """
    result = occurrences

    if category_ids:
        result = [o for o in result if o['category_id'] in category_ids]

    if has_substitute == 'yes':
        result = [o for o in result if o.get('substitute_id')]
    elif has_substitute == 'no':
        result = [o for o in result if not o.get('substitute_id')]

    return result


def get_absence_history(absence_id: int) -> list[AbsenceHistory]:
    """Get change history for an absence.

    Args:
        absence_id: Absence ID.

    Returns:
        List of history records, newest first.
    """
    return db.session.scalars(
        select(AbsenceHistory)
        .filter_by(absence_id=absence_id)
        .options(selectinload(AbsenceHistory.changed_by))
        .order_by(AbsenceHistory.changed_at.desc())
    ).all()


def get_absence_exception_counts(absence: Absence) -> dict:
    """Get exception statistics for a recurring absence.

    Args:
        absence: Absence instance.

    Returns:
        Dict with exception_count, deleted_count, modified_count.
    """
    def count_exceptions(**filters) -> int:
        return db.session.scalar(
            select(func.count())
            .select_from(RecurrenceException)
            .filter_by(absence_id=absence.id, **filters)
        )

    return {
        'exception_count': count_exceptions(),
        'deleted_count': count_exceptions(exception_type='deleted'),
        'modified_count': count_exceptions(exception_type='modified')
    }


def get_deleted_occurrence_dates(absence: Absence) -> list[dict]:
    """Get the dates of deleted occurrences for a recurring absence.

    Args:
        absence: Absence instance.

    Returns:
        List of dicts with a ``date`` key, ordered by exception date.
    """
    deleted = db.session.scalars(
        absence.exceptions.select()
        .filter_by(exception_type='deleted')
        .order_by(RecurrenceException.exception_date)
    ).all()
    return [{'date': exc.exception_date} for exc in deleted]


def get_absence_by_id(absence_id: int) -> Absence | None:
    """Get absence by ID or None if not found.

    Args:
        absence_id: Absence ID.

    Returns:
        Absence instance or None.
    """
    return db.session.get(Absence, absence_id)
