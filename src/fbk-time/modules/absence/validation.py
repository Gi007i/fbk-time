"""Field-level validation for absence management.

Holds the stateless field validators. Conflict detection and time-slot
overlap checks live in ``conflicts`` and the low-level slot algebra in
``timeslots``; both are re-exported here so callers keep a single import
surface.
"""

from datetime import date
from typing import Optional, Tuple

from core.extensions import db
from modules.category.models import Category

from .conflicts import (
    ConflictResult,
    check_absence_conflicts,
    substitute_slot_available,
    validate_time_slot_overlap,
)

__all__ = [
    'ConflictResult',
    'check_absence_conflicts',
    'substitute_slot_available',
    'validate_time_slot_overlap',
    'validate_substitute_required',
    'validate_category_assignable',
    'validate_substitute_not_self',
    'validate_date_range',
]


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


def validate_category_assignable(
    category_id: int,
    current_category_id: Optional[int]
) -> Tuple[bool, Optional[str]]:
    """Reject newly assigning a disabled category.

    Disabled categories remain visible on legacy records so that
    existing data keeps rendering, but they must not be freshly
    assigned to another record. A record that already carries a
    disabled category may keep it (no-op change) so that unrelated
    edits on old data do not fail.

    Args:
        category_id: The desired category ID (new value from the form).
        current_category_id: The category ID currently stored on the
            record. ``None`` for create operations.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if category_id == current_category_id:
        return True, None

    category = db.session.get(Category, category_id)
    if not category:
        return False, 'Ungültige Kategorie'

    if not category.active:
        return False, (
            f'Kategorie "{category.name}" ist deaktiviert und kann nicht '
            f'neu zugewiesen werden'
        )

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
