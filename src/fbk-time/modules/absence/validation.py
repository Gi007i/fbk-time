"""Validation service for absence management.

Provides conflict detection and validation for absences.
"""

from datetime import date
from typing import List, Optional, Tuple

from core.extensions import db
from modules.absence.models import Absence
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from utils.helpers import format_date_for_user


class ConflictResult:
    """Result of a conflict check."""

    def __init__(self, has_conflicts: bool = False):
        self.has_conflicts = has_conflicts
        self.user_conflicts: List[Absence] = []
        self.substitute_conflicts: List[Absence] = []
        self.cross_substitution_warning: bool = False
        self.messages: List[str] = []

    def add_user_conflict(self, absence: Absence, message: str):
        """Add a user conflict."""
        self.has_conflicts = True
        self.user_conflicts.append(absence)
        self.messages.append(message)

    def add_substitute_conflict(self, absence: Absence, message: str):
        """Add a substitute conflict."""
        self.has_conflicts = True
        self.substitute_conflicts.append(absence)
        self.messages.append(message)

    def add_warning(self, message: str):
        """Add a warning message (not blocking)."""
        self.messages.append(message)


def check_absence_conflicts(
    user_id: int,
    start_date: date,
    end_date: date,
    exclude_absence_id: Optional[int] = None,
    substitute_id: Optional[int] = None
) -> ConflictResult:
    """Check for conflicts with existing absences.

    Args:
        user_id: User for whom the absence is being created.
        start_date: Start date of the new absence.
        end_date: End date of the new absence.
        exclude_absence_id: ID of absence to exclude (for edits).
        substitute_id: ID of proposed substitute user.

    Returns:
        ConflictResult with any found conflicts.
    """
    result = ConflictResult()

    user_absences = _find_overlapping_absences(
        user_id, start_date, end_date, exclude_absence_id
    )

    for absence in user_absences:
        result.add_user_conflict(
            absence,
            f'Überschneidung mit bestehender Abwesenheit vom '
            f'{format_date_for_user(absence.start_date)} bis '
            f'{format_date_for_user(absence.end_date)}'
        )

    if substitute_id:
        substitute_absences = _find_overlapping_absences(
            substitute_id, start_date, end_date, exclude_absence_id
        )

        for absence in substitute_absences:
            result.add_substitute_conflict(
                absence,
                f'Vertretung {_get_user_name(substitute_id)} ist im Zeitraum '
                f'{format_date_for_user(absence.start_date)} bis '
                f'{format_date_for_user(absence.end_date)} selbst abwesend'
            )

        existing_assignments = _find_substitute_assignments(
            substitute_id, start_date, end_date, exclude_absence_id
        )
        for absence in existing_assignments:
            result.add_warning(
                f'{_get_user_name(substitute_id)} vertritt bereits '
                f'{_get_user_name(absence.user_id)} im Zeitraum '
                f'{format_date_for_user(absence.start_date)} bis '
                f'{format_date_for_user(absence.end_date)}'
            )

        if _check_cross_substitution(user_id, substitute_id, start_date, end_date):
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
    category = db.session.get(Category,category_id)
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


def get_available_substitutes(
    user_id: int,
    start_date: date,
    end_date: date
) -> List[User]:
    """Get list of users available as substitutes for given period.

    Excludes users who have absences in the given period.

    Args:
        user_id: User for whom substitute is needed (excluded).
        start_date: Start date of absence period.
        end_date: End date of absence period.

    Returns:
        List of available User records.
    """
    all_users = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]),
        User.id != user_id
    ).order_by(User.name).all()

    available = []
    for user in all_users:
        overlapping = _find_overlapping_absences(user.id, start_date, end_date)
        if not overlapping:
            available.append(user)

    return available


def _find_overlapping_absences(
    user_id: int,
    start_date: date,
    end_date: date,
    exclude_id: Optional[int] = None
) -> List[Absence]:
    """Find absences that overlap with the given date range.

    Two date ranges overlap if: start1 <= end2 AND end1 >= start2
    """
    query = Absence.query.filter(
        Absence.user_id == user_id,
        Absence.start_date <= end_date,
        Absence.end_date >= start_date
    )

    if exclude_id:
        query = query.filter(Absence.id != exclude_id)

    return query.all()


def _find_substitute_assignments(
    substitute_id: int,
    start_date: date,
    end_date: date,
    exclude_id: Optional[int] = None
) -> List[Absence]:
    """Find absences where the person is assigned as substitute.

    Returns absences that overlap with the given date range and have
    substitute_id as their substitute.
    """
    query = Absence.query.filter(
        Absence.substitute_id == substitute_id,
        Absence.start_date <= end_date,
        Absence.end_date >= start_date
    )

    if exclude_id:
        query = query.filter(Absence.id != exclude_id)

    return query.all()


def _check_cross_substitution(
    user_id: int,
    substitute_id: int,
    start_date: date,
    end_date: date
) -> bool:
    """Check if there's a cross-substitution situation.

    Returns True if the substitute has an absence where the user
    is their substitute in an overlapping period.
    """
    cross_absences = Absence.query.filter(
        Absence.user_id == substitute_id,
        Absence.substitute_id == user_id,
        Absence.start_date <= end_date,
        Absence.end_date >= start_date
    ).first()

    return cross_absences is not None


def _get_user_name(user_id: int) -> str:
    """Get user name by ID."""
    user = db.session.get(User,user_id)
    return user.name if user else f'Person {user_id}'
