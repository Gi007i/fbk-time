"""History tracking service for absence management.

Tracks changes to absence records for audit purposes.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from flask_login import current_user

from core.extensions import db
from modules.absence.models import Absence, AbsenceHistory
from utils.helpers import format_date_for_user


def _get_current_user_id() -> Optional[int]:
    """Get current user ID if in request context, None otherwise."""
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
    except RuntimeError:
        pass
    return None


def track_change(
    absence: Absence,
    field_name: str,
    old_value: Any,
    new_value: Any
) -> Optional[AbsenceHistory]:
    """Record a single field change for an absence.

    Args:
        absence: The absence being modified.
        field_name: Name of the changed field.
        old_value: Previous value (will be converted to string).
        new_value: New value (will be converted to string).

    Returns:
        Created AbsenceHistory record or None if values are equal.
    """
    old_str = _format_value(old_value)
    new_str = _format_value(new_value)

    if old_str == new_str:
        return None

    history = AbsenceHistory(
        absence_id=absence.id,
        changed_by_id=_get_current_user_id(),
        changed_at=datetime.now(timezone.utc),
        field_name=field_name,
        old_value=old_str,
        new_value=new_str
    )
    db.session.add(history)
    return history


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
        'notes': ('Notizen', str)
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


def get_absence_history(absence_id: int) -> list:
    """Get all history entries for an absence, ordered by date descending.

    Args:
        absence_id: ID of the absence.

    Returns:
        List of AbsenceHistory records.
    """
    return AbsenceHistory.query.filter_by(
        absence_id=absence_id
    ).order_by(
        AbsenceHistory.changed_at.desc()
    ).all()


def _format_value(value: Any) -> Optional[str]:
    """Convert any value to string for storage."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 'Ja' if value else 'Nein'
    if hasattr(value, 'strftime'):
        if hasattr(value, 'hour'):
            return value.strftime('%H:%M')
        return format_date_for_user(value)
    return str(value)[:255]


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
