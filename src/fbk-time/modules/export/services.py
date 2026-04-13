"""Export services.

Provides business logic for building export queries and data preparation.
"""

from datetime import date
from calendar import monthrange
from typing import List, Optional

from core.extensions import db
from modules.absence.models import Absence
from modules.absence.recurrence import recurrence_service
from modules.auth.models import User, UserStatus
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
    user_ids: Optional[List[int]] = None
):
    """Build SQLAlchemy query for absences with filters.

    Category filtering is deliberately not performed here: it must be
    applied to expanded occurrences so that modified recurring exceptions
    are filtered by their effective category. Recurring absences are
    always considered; callers apply range filters post-expansion.

    Args:
        from_date: Start date filter.
        to_date: End date filter.
        user_id: Filter by a single user ID (mutually exclusive with user_ids).
        user_ids: Filter by a list of user IDs (mutually exclusive with user_id).

    Returns:
        SQLAlchemy query object.
    """
    # The role filter is intentionally omitted so that an Admin or Manager
    # who owns absences (e.g. their own calendar entries) can still export
    # them. Authorisation is enforced in the export views before this
    # query runs.
    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        Category.active == True
    )

    if user_id is not None:
        query = query.filter(Absence.user_id == user_id)

    if user_ids is not None:
        query = query.filter(Absence.user_id.in_(user_ids))

    if to_date is not None:
        query = query.filter(Absence.start_date <= to_date)

    if from_date is not None:
        query = query.filter(
            db.or_(
                db.and_(
                    Absence.is_recurring == False,
                    Absence.end_date >= from_date
                ),
                db.and_(
                    Absence.is_recurring == True,
                    db.or_(
                        Absence.recurrence_end_date >= from_date,
                        Absence.recurrence_end_date.is_(None)
                    )
                )
            )
        )

    return query


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


def get_absences_for_export(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user_id: Optional[int] = None,
    order_desc: bool = False
) -> list[Absence]:
    """Get filtered absences for export, ordered by start date.

    Args:
        from_date: Start date filter.
        to_date: End date filter.
        user_id: Filter by user ID.
        order_desc: If True, order by start_date DESC; otherwise ASC.

    Returns:
        List of absences.
    """
    query = build_absence_query(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id
    )

    if order_desc:
        return query.order_by(Absence.start_date.desc()).all()
    return query.order_by(Absence.start_date).all()


def build_export_occurrences(
    from_date: date,
    to_date: date,
    user_id: Optional[int] = None,
    category_id: Optional[int] = None,
    has_substitute: Optional[str] = None,
    order_desc: bool = False
) -> list[dict]:
    """Load, expand and filter occurrences ready for rendering.

    This is the single entry point used by all export endpoints so that
    category and substitute filters operate on effective occurrence state
    (not on parent absences).

    Args:
        from_date: Start of the range.
        to_date: End of the range.
        user_id: Optional user filter.
        category_id: Optional effective-category filter.
        has_substitute: 'yes', 'no', or None.
        order_desc: Sort descending by date when True.

    Returns:
        List of occurrence dicts sorted by date (and user name for ties).
    """
    from modules.absence.services import filter_occurrences

    absences = get_absences_for_export(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id
    )

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, from_date, to_date
    )

    occurrences = filter_occurrences(
        occurrences,
        category_id=category_id,
        has_substitute=has_substitute
    )

    occurrences.sort(
        key=lambda o: (o['date'], o['user'].name if o.get('user') else ''),
        reverse=order_desc
    )
    return occurrences
