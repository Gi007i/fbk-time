"""Absence management services.

Provides business logic for absence CRUD operations,
orchestrating validation, history tracking, and recurrence handling.
"""

from datetime import date
from typing import Literal, Optional, Tuple

from flask import current_app, abort
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from core.extensions import db
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from utils.helpers import format_date_for_user

from .models import Absence, AbsenceHistory, RecurrenceException
from .validation import (
    check_absence_conflicts,
    validate_substitute_required,
    validate_substitute_not_self,
    validate_category_assignable,
    validate_date_range,
    validate_time_slot_overlap,
    substitute_slot_available,
    ConflictResult,
)
from .history import (
    create_initial_history,
    track_absence_changes,
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
        current_category_id: For update operations, the category ID
            currently stored on the record. ``None`` for create. Used
            to reject freshly assigning a disabled category while still
            allowing unrelated edits on records that already carry one.

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
    if recurrence_data and recurrence_data.get('is_recurring'):
        rrule_str = recurrence_data.get('rrule')
        recurrence_end = recurrence_data.get('recurrence_end_date')

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

    # Re-validate time slot overlap after flush to reduce TOCTOU race window.
    # After flush, this session holds the write lock in SQLite WAL mode,
    # preventing concurrent writers from committing between check and insert.
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

    Called after a series edit that may change start_date, rrule or
    recurrence_end_date. An exception is considered orphaned when its
    date is no longer produced by the (new) RRULE. The raw pattern
    is consulted (``is_date_in_rrule``) rather than the expanded
    occurrence list so that 'deleted' exceptions on dates that are
    still part of the series survive unrelated edits.
    """
    if not absence.is_recurring or not absence.rrule:
        for exc in list(absence.exceptions.all()):
            db.session.delete(exc)
        return

    for exc in list(absence.exceptions.all()):
        if not recurrence_service.is_date_in_rrule(
            absence, exc.exception_date
        ):
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
        'notes': notes.strip() if notes else None
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
        # After rollback the session is clean, but the in-memory
        # ``absence`` still carries the mutated Python attributes from
        # the assignment block above. Refresh it from the database so
        # that any subsequent template rendering in the caller (edit
        # view) shows the committed state rather than the rejected one.
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

    Validates the effective (merged) state of the occurrence against
    the same rules as a normal absence: substitute requirements,
    self-substitute, time-slot overlaps. Delegates the DB mutation
    to recurrence_service.modify_occurrence which compares against
    the parent and stores only real overrides.

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

    if time_type not in ('all_day', 'morning', 'afternoon'):
        raise ValueError(f'Invalid time_type: {time_type!r}')

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
        is_all_day=time_type == 'all_day',
        is_half_day_morning=time_type == 'morning',
        is_half_day_afternoon=time_type == 'afternoon',
        exclude_absence_id=absence.id
    )
    if not is_valid:
        raise ValueError(error)

    warnings = []
    if effective_substitute_id is not None:
        if not substitute_slot_available(
            effective_substitute_id,
            occurrence_date,
            is_all_day=time_type == 'all_day',
            is_half_day_morning=time_type == 'morning',
            is_half_day_afternoon=time_type == 'afternoon',
            exclude_absence_id=absence.id
        ):
            raise ValueError(
                f'Vertretung ist am {format_date_for_user(occurrence_date)} '
                f'selbst abwesend'
            )
        warnings = check_absence_conflicts(
            user_id=absence.user_id,
            start_date=occurrence_date,
            end_date=occurrence_date,
            exclude_absence_id=absence.id,
            substitute_id=effective_substitute_id,
            time_flags={
                'is_all_day': time_type == 'all_day',
                'is_half_day_morning': time_type == 'morning',
                'is_half_day_afternoon': time_type == 'afternoon',
            }
        ).messages

    effective_before = {
        'category_id': before_data.get('category_id'),
        'time_type': time_flags_to_type(before_data),
        'substitute_id': before_data.get('substitute_id'),
        'notes': before_data.get('notes')
    }

    recurrence_service.modify_occurrence(
        absence, occurrence_date, effective_state
    )

    after_data = recurrence_service.get_occurrence_data(
        absence, occurrence_date
    ) or {}
    effective_after = {
        'category_id': after_data.get('category_id'),
        'time_type': time_flags_to_type(after_data),
        'substitute_id': after_data.get('substitute_id'),
        'notes': after_data.get('notes')
    }

    track_occurrence_modifications(
        absence, occurrence_date, effective_before, effective_after
    )

    return f'Termin am {format_date_for_user(occurrence_date)} wurde geändert.', warnings


def time_flags_to_type(occ_data: dict) -> Literal['all_day', 'morning', 'afternoon']:
    """Derive the time_type enum value from merged occurrence data."""
    if occ_data.get('is_half_day_morning'):
        return 'morning'
    if occ_data.get('is_half_day_afternoon'):
        return 'afternoon'
    return 'all_day'


def restore_occurrence(absence: Absence, occurrence_date: date) -> Tuple[str, list]:
    """Restore a deleted or modified occurrence to its series defaults.

    Validates that the restored occurrence does not conflict with
    existing absences or substitute availability before removing
    the exception.

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

    parent_time_type = recurrence_service.parent_time_type(absence)
    is_valid, error = validate_time_slot_overlap(
        user_id=absence.user_id,
        start_date=occurrence_date,
        end_date=occurrence_date,
        is_all_day=parent_time_type == 'all_day',
        is_half_day_morning=parent_time_type == 'morning',
        is_half_day_afternoon=parent_time_type == 'afternoon',
        exclude_absence_id=absence.id
    )
    if not is_valid:
        raise ValueError(error)

    warnings = []
    if absence.substitute_id is not None:
        if not substitute_slot_available(
            absence.substitute_id,
            occurrence_date,
            is_all_day=absence.is_all_day,
            is_half_day_morning=absence.is_half_day_morning,
            is_half_day_afternoon=absence.is_half_day_afternoon,
            start_time=absence.start_time,
            end_time=absence.end_time,
            exclude_absence_id=absence.id
        ):
            raise ValueError(
                f'Vertretung ist am {format_date_for_user(occurrence_date)} '
                f'selbst abwesend'
            )
        warnings = check_absence_conflicts(
            user_id=absence.user_id,
            start_date=occurrence_date,
            end_date=occurrence_date,
            exclude_absence_id=absence.id,
            substitute_id=absence.substitute_id,
            time_flags={
                'is_all_day': absence.is_all_day,
                'is_half_day_morning': absence.is_half_day_morning,
                'is_half_day_afternoon': absence.is_half_day_afternoon,
                'start_time': absence.start_time,
                'end_time': absence.end_time,
            }
        ).messages

    was_deleted = exception.exception_type == 'deleted'
    db.session.delete(exception)
    track_occurrence_restoration(absence, occurrence_date)

    if was_deleted:
        return f'Termin am {format_date_for_user(occurrence_date)} wurde wiederhergestellt.', warnings
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
    return User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all()


def get_active_categories() -> list[Category]:
    """Get list of active categories for forms.

    Returns:
        List of active categories ordered by sort_order.
    """
    return Category.query.filter_by(active=True).order_by(Category.sort_order).all()


def get_substitute_choices(exclude_user_id: Optional[int] = None) -> list[User]:
    """Get list of users eligible as substitutes.

    Args:
        exclude_user_id: User ID to exclude from list.

    Returns:
        List of users who can be substitutes.
    """
    query = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    )

    if exclude_user_id:
        query = query.filter(User.id != exclude_user_id)

    return query.order_by(User.name).all()


def get_absences_list(
    date_from: date,
    date_to: date,
    user_ids: Optional[list[int]] = None
) -> list[Absence]:
    """Get absences overlapping a date range for active users.

    Category and substitute filtering is performed on the expanded
    occurrence level (post-expand), so that modified occurrences of
    recurring series are filtered by their effective state.

    Args:
        date_from: Start date of range.
        date_to: End date of range.
        user_ids: Optional person filter (any of the given IDs).

    Returns:
        List of absences whose master record overlaps the range.
    """
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) &
            (Absence.start_date <= date_to) &
            (Absence.end_date >= date_from),
            (Absence.is_recurring == True) &
            (Absence.start_date <= date_to) &
            ((Absence.recurrence_end_date >= date_from) | (Absence.recurrence_end_date.is_(None)))
        )
    )

    if user_ids:
        query = query.filter(Absence.user_id.in_(user_ids))

    return query.all()


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
    return AbsenceHistory.query.filter_by(
        absence_id=absence_id
    ).order_by(AbsenceHistory.changed_at.desc()).all()


def get_absence_exception_counts(absence: Absence) -> dict:
    """Get exception statistics for a recurring absence.

    Args:
        absence: Absence instance.

    Returns:
        Dict with exception_count, deleted_count, modified_count.
    """
    return {
        'exception_count': absence.exceptions.count(),
        'deleted_count': absence.exceptions.filter_by(exception_type='deleted').count(),
        'modified_count': absence.exceptions.filter_by(exception_type='modified').count()
    }


def get_deleted_occurrence_dates(absence: Absence) -> list[dict]:
    """Get the dates of deleted occurrences for a recurring absence.

    Args:
        absence: Absence instance.

    Returns:
        List of dicts with a ``date`` key, ordered by exception date.
    """
    return [
        {'date': exc.exception_date}
        for exc in absence.exceptions.filter_by(exception_type='deleted')
        .order_by(RecurrenceException.exception_date).all()
    ]


def get_absence_by_id(absence_id: int) -> Absence | None:
    """Get absence by ID or None if not found.

    Args:
        absence_id: Absence ID.

    Returns:
        Absence instance or None.
    """
    return db.session.get(Absence, absence_id)


def get_recurring_absences_for_active_users() -> list[Absence]:
    """Get all recurring absences for active/managed users.

    Returns:
        List of recurring absences.
    """
    return Absence.query.join(
        User, Absence.user_id == User.id
    ).filter(
        Absence.is_recurring == True,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]),
        User.role == UserRole.USER
    ).all()
