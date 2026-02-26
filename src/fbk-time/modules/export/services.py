"""Export services.

Provides business logic for building export queries and data preparation.
"""

from datetime import date
from calendar import monthrange
from typing import Optional

from core.extensions import db
from modules.absence.models import Absence
from modules.absence.recurrence import recurrence_service
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category


def get_default_date_range() -> tuple[date, date]:
    """Get default date range for exports (current month).

    Returns:
        Tuple of (from_date, to_date).
    """
    today = date.today()
    from_date = date(today.year, today.month, 1)
    _, days = monthrange(from_date.year, from_date.month)
    to_date = date(from_date.year, from_date.month, days)
    return from_date, to_date


def build_absence_query(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user_id: Optional[int] = None,
    category_id: Optional[int] = None,
    include_recurring: bool = True
):
    """Build SQLAlchemy query for absences with filters.

    Args:
        from_date: Start date filter.
        to_date: End date filter.
        user_id: Filter by user ID.
        category_id: Filter by category ID.
        include_recurring: Include recurring absences.

    Returns:
        SQLAlchemy query object.
    """
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True
    )

    if user_id:
        query = query.filter(Absence.user_id == user_id)

    if category_id:
        query = query.filter(Absence.category_id == category_id)

    if from_date and to_date:
        query = query.filter(
            Absence.start_date <= to_date
        ).filter(
            db.or_(
                Absence.end_date >= from_date,
                db.and_(
                    Absence.is_recurring == True,
                    db.or_(
                        Absence.recurrence_end_date >= from_date,
                        Absence.recurrence_end_date.is_(None)
                    )
                )
            ) if include_recurring else Absence.end_date >= from_date
        )
    elif from_date:
        query = query.filter(
            db.or_(
                Absence.end_date >= from_date,
                db.and_(
                    Absence.is_recurring == True,
                    db.or_(
                        Absence.recurrence_end_date >= from_date,
                        Absence.recurrence_end_date.is_(None)
                    )
                )
            ) if include_recurring else Absence.end_date >= from_date
        )
    elif to_date:
        query = query.filter(Absence.start_date <= to_date)

    return query


def get_export_occurrences(
    absences: list,
    from_date: date,
    to_date: date
) -> list[dict]:
    """Expand absences to individual occurrences for export.

    Args:
        absences: List of absence objects.
        from_date: Start date.
        to_date: End date.

    Returns:
        List of occurrence dicts.
    """
    return recurrence_service.get_all_occurrences_for_range(
        absences, from_date, to_date
    )


def build_pdf_title(user_id: Optional[int], category_id: Optional[int]) -> str:
    """Build title for PDF export.

    Args:
        user_id: Optional user filter.
        category_id: Optional category filter.

    Returns:
        Title string for PDF.
    """
    title_parts = ['Abwesenheitsübersicht']

    if user_id:
        user = db.session.get(User, user_id)
        if user:
            title_parts.append(f'- {user.name}')

    if category_id:
        category = db.session.get(Category, category_id)
        if category:
            title_parts.append(f'({category.name})')

    return ' '.join(title_parts)


def build_ical_name(user_id: Optional[int]) -> str:
    """Build calendar name for iCal export.

    Args:
        user_id: Optional user filter.

    Returns:
        Calendar name string.
    """
    if user_id:
        user = db.session.get(User, user_id)
        if user:
            return f'Abwesenheiten - {user.name}'

    return 'FBK-Time Abwesenheiten'


def get_user_absences_ordered(user_id: int) -> list[Absence]:
    """Get all absences for a user ordered by start date.

    Args:
        user_id: User ID.

    Returns:
        List of absences ordered by start_date.
    """
    return Absence.query.filter(
        Absence.user_id == user_id
    ).order_by(Absence.start_date).all()


def get_absences_for_export(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user_id: Optional[int] = None,
    category_id: Optional[int] = None,
    order_desc: bool = False
) -> list[Absence]:
    """Get filtered absences for export, ordered by start date.

    Args:
        from_date: Start date filter.
        to_date: End date filter.
        user_id: Filter by user ID.
        category_id: Filter by category ID.
        order_desc: If True, order by start_date DESC; otherwise ASC.

    Returns:
        List of absences.
    """
    query = build_absence_query(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        category_id=category_id,
        include_recurring=True
    )

    if order_desc:
        return query.order_by(Absence.start_date.desc()).all()
    return query.order_by(Absence.start_date).all()
