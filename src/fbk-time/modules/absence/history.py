"""Audit trail of changes to absence records."""

from datetime import date, datetime, timezone
from typing import Optional

from core.auth import current_user
from core.db import db
from modules.absence.models import Absence, AbsenceHistory, RecurrenceException
from modules.absence.recurrence import recurrence_service
from utils.helpers import format_date_for_user


def _get_current_user_id() -> Optional[int]:
    """Get current user ID if in request context, None otherwise."""
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
    except RuntimeError:
        pass
    return None


def track_absence_changes(absence: Absence, form_data: dict) -> list:
    """Compare absence with form data and track all changes.

    Args:
        absence: Existing absence record.
        form_data: Dictionary with new values from form.

    Returns:
        List of created AbsenceHistory records.
    """
    changes = []
    field_mappings = {
        'user_id': ('Person', _get_user_name),
        'category_id': ('Kategorie', _get_category_name),
        'start_date': ('Startdatum', _format_date),
        'end_date': ('Enddatum', _format_date),
        'start_time': ('Startzeit', _format_time),
        'end_time': ('Endzeit', _format_time),
        'is_all_day': ('Ganztags', _format_bool),
        'is_half_day_morning': ('Halbtags Vormittag', _format_bool),
        'is_half_day_afternoon': ('Halbtags Nachmittag', _format_bool),
        'substitute_id': ('Vertretung', _get_user_name),
        'notes': ('Notizen', str),
        'is_recurring': ('Serie', _format_bool),
        'rrule': ('Serienmuster', recurrence_service.describe_pattern_safe),
        'recurrence_end_date': ('Serienende', _format_date)
    }

    for field, (display_name, formatter) in field_mappings.items():
        if field not in form_data:
            continue

        old_value = getattr(absence, field, None)
        new_value = form_data.get(field)

        old_display = formatter(old_value) if old_value is not None else None
        new_display = formatter(new_value) if new_value is not None else None

        if old_display != new_display:
            history = AbsenceHistory(
                absence_id=absence.id,
                changed_by_id=_get_current_user_id(),
                changed_at=datetime.now(timezone.utc),
                field_name=display_name,
                old_value=_truncate(old_display),
                new_value=_truncate(new_display)
            )
            db.session.add(history)
            changes.append(history)

    return changes


def track_occurrence_modifications(
    absence: Absence,
    occurrence_date: date,
    effective_before: dict,
    effective_after: dict
) -> list:
    """Record per-field changes for a modified occurrence of a series.

    Compares the effective state before and after applying the
    modifications and emits one AbsenceHistory row per changed field.
    The field name carries the occurrence date so that the audit trail
    remains attributable to a single day.

    Args:
        absence: The parent recurring absence.
        occurrence_date: Date of the affected occurrence.
        effective_before: Effective merged state before the change.
        effective_after: Effective merged state after the change.

    Returns:
        List of created AbsenceHistory records.
    """
    date_label = format_date_for_user(occurrence_date)
    changes = []
    field_mappings = {
        'category_id': ('Kategorie', _get_category_name),
        'time_type': ('Zeittyp', _format_time_type),
        'substitute_id': ('Vertretung', _get_user_name),
        'notes': ('Notizen', str)
    }

    for field, (display_name, formatter) in field_mappings.items():
        old_value = effective_before.get(field)
        new_value = effective_after.get(field)

        old_display = formatter(old_value) if old_value is not None else None
        new_display = formatter(new_value) if new_value is not None else None

        if old_display == new_display:
            continue

        history = AbsenceHistory(
            absence_id=absence.id,
            changed_by_id=_get_current_user_id(),
            changed_at=datetime.now(timezone.utc),
            field_name=f'Termin {date_label} - {display_name}',
            old_value=_truncate(old_display),
            new_value=_truncate(new_display)
        )
        db.session.add(history)
        changes.append(history)

    return changes


def track_occurrence_deletion(absence: Absence, occurrence_date: date) -> AbsenceHistory:
    """Record a deleted occurrence in the audit trail.

    Args:
        absence: The parent recurring absence.
        occurrence_date: Date of the removed occurrence.

    Returns:
        Created AbsenceHistory record.
    """
    history = AbsenceHistory(
        absence_id=absence.id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name=f'Termin {format_date_for_user(occurrence_date)} - Entfernt',
        old_value='vorhanden',
        new_value='entfernt'
    )
    db.session.add(history)
    return history


def track_occurrence_restoration(absence: Absence, occurrence_date: date) -> AbsenceHistory:
    """Record a restored occurrence in the audit trail.

    Args:
        absence: The parent recurring absence.
        occurrence_date: Date of the restored occurrence.

    Returns:
        Created AbsenceHistory record.
    """
    history = AbsenceHistory(
        absence_id=absence.id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name=f'Termin {format_date_for_user(occurrence_date)} - Wiederhergestellt',
        old_value='entfernt',
        new_value='vorhanden'
    )
    db.session.add(history)
    return history


def track_exception_pruned(
    absence: Absence, exception: RecurrenceException
) -> AbsenceHistory:
    """Record an exception dropped because its date left the series.

    Args:
        absence: The parent absence after the series change.
        exception: The exception being removed.

    Returns:
        Created AbsenceHistory record.
    """
    old_value = 'entfernt' if exception.exception_type == 'deleted' else 'geändert'
    history = AbsenceHistory(
        absence_id=absence.id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name=f'Termin {format_date_for_user(exception.exception_date)} - Ausnahme',
        old_value=old_value,
        new_value='verworfen (nicht mehr Teil der Serie)'
    )
    db.session.add(history)
    return history


def create_initial_history(absence: Absence) -> AbsenceHistory:
    """Create initial history entry when absence is created.

    Args:
        absence: Newly created absence.

    Returns:
        Created AbsenceHistory record.
    """
    history = AbsenceHistory(
        absence_id=absence.id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name='Erstellung',
        old_value=None,
        new_value='Abwesenheit erstellt'
    )
    db.session.add(history)
    return history


def track_substitute_cleared_on_user_delete(absence_id: int) -> AbsenceHistory:
    """Record a substitute removed because the substitute account was deleted.

    Omits the deleted person's name so a removed account is not
    re-persisted in the audit trail.
    """
    history = AbsenceHistory(
        absence_id=absence_id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name='Vertretung',
        old_value='vorhanden',
        new_value='entfernt (Benutzer gelöscht)'
    )
    db.session.add(history)
    return history


def track_occurrence_substitute_cleared(
    absence_id: int, occurrence_date: date
) -> AbsenceHistory:
    """Record a per-occurrence substitute removed on account deletion."""
    history = AbsenceHistory(
        absence_id=absence_id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name=f'Termin {format_date_for_user(occurrence_date)} - Vertretung',
        old_value='vorhanden',
        new_value='entfernt (Benutzer gelöscht)'
    )
    db.session.add(history)
    return history


def track_category_transfer(
    absence_id: int, old_category_name: str, new_category_name: str
) -> AbsenceHistory:
    """Record a category reassignment caused by deleting the old category."""
    history = AbsenceHistory(
        absence_id=absence_id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name='Kategorie',
        old_value=_truncate(f'{old_category_name} (gelöscht)'),
        new_value=_truncate(new_category_name)
    )
    db.session.add(history)
    return history


def track_occurrence_category_transfer(
    absence_id: int,
    occurrence_date: date,
    old_category_name: str,
    new_category_name: str
) -> AbsenceHistory:
    """Record a per-occurrence category reassignment on category deletion."""
    history = AbsenceHistory(
        absence_id=absence_id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name=f'Termin {format_date_for_user(occurrence_date)} - Kategorie',
        old_value=_truncate(f'{old_category_name} (gelöscht)'),
        new_value=_truncate(new_category_name)
    )
    db.session.add(history)
    return history


def _format_time_type(value) -> str:
    """Format the time_type enum for display."""
    return {
        'all_day': 'Ganztags',
        'morning': 'Halbtags Vormittag',
        'afternoon': 'Halbtags Nachmittag',
        'custom_time': 'Uhrzeit der Serie'
    }.get(value, str(value))


def _format_date(value) -> Optional[str]:
    """Format date for display."""
    if value is None:
        return None
    if hasattr(value, 'strftime'):
        return format_date_for_user(value)
    return str(value)


def _format_time(value) -> Optional[str]:
    """Format time for display."""
    if value is None:
        return None
    if hasattr(value, 'strftime'):
        return value.strftime('%H:%M')
    return str(value)


def _format_bool(value) -> str:
    """Format boolean for display."""
    return 'Ja' if value else 'Nein'


def _get_user_name(user_id: int) -> Optional[str]:
    """Get user name by ID."""
    if not user_id:
        return None
    from modules.auth.models import User
    user = db.session.get(User, user_id)
    return user.name if user else f'ID {user_id}'


def _get_category_name(category_id: int) -> Optional[str]:
    """Get category name by ID."""
    if not category_id:
        return None
    from modules.category.models import Category
    category = db.session.get(Category, category_id)
    return category.name if category else f'ID {category_id}'


def _truncate(value: Optional[str], max_length: int = 255) -> Optional[str]:
    """Truncate string to maximum length."""
    if value is None:
        return None
    if len(value) > max_length:
        return value[:max_length - 3] + '...'
    return value
