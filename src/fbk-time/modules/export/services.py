"""Export queries and occurrence preparation."""

from datetime import date
from calendar import monthrange
from typing import List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from core.db import db
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
    user_ids: Optional[List[int]] = None
):
    """Build SQLAlchemy query for absences with filters.

    Category filtering is left to the expanded occurrences, so modified
    occurrences of a series are filtered by their effective category.

    Args:
        from_date: Start date filter.
        to_date: End date filter.
        user_ids: Optional person filter (any of the given IDs); an empty
            list means no person filter.

    Returns:
        SQLAlchemy select statement.
    """
    # Same visibility as the absence list and team views: an account promoted
    # from USER keeps its old absences, which must not surface only here.
    query = select(Absence).join(
        User, Absence.user_id == User.id
    ).where(
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]),
        User.role == UserRole.USER
    ).options(
        selectinload(Absence.user),
        selectinload(Absence.category),
        selectinload(Absence.substitute)
    )

    if user_ids:
        query = query.where(Absence.user_id.in_(user_ids))

    if to_date is not None:
        query = query.where(Absence.start_date <= to_date)

    if from_date is not None:
        query = query.where(
            or_(
                and_(
                    Absence.is_recurring == False,
                    Absence.end_date >= from_date
                ),
                and_(
                    Absence.is_recurring == True,
                    or_(
                        Absence.recurrence_end_date >= from_date,
                        Absence.recurrence_end_date.is_(None)
                    )
                )
            )
        )

    return query


def build_pdf_title(
    user_ids: Optional[List[int]], category_ids: Optional[List[int]]
) -> str:
    """Build title for PDF export.

    A person/category name is only appended when exactly one is filtered;
    multiple selections keep the generic title to avoid an unwieldy heading.

    Args:
        user_ids: Optional person filter.
        category_ids: Optional category filter.

    Returns:
        Title string for PDF.
    """
    title_parts = ['Abwesenheitsübersicht']

    if user_ids and len(user_ids) == 1:
        user = db.session.get(User, user_ids[0])
        if user:
            title_parts.append(f'- {user.name}')

    if category_ids and len(category_ids) == 1:
        category = db.session.get(Category, category_ids[0])
        if category:
            title_parts.append(f'({category.name})')

    return ' '.join(title_parts)


def build_ical_name(user_ids: Optional[List[int]]) -> str:
    """Build calendar name for iCal export.

    Args:
        user_ids: Optional person filter (name shown only for a single one).

    Returns:
        Calendar name string.
    """
    if user_ids and len(user_ids) == 1:
        user = db.session.get(User, user_ids[0])
        if user:
            return f'Abwesenheiten - {user.name}'

    return 'FBK-Time Abwesenheiten'


def build_filter_summary(
    user_ids: Optional[List[int]] = None,
    category_ids: Optional[List[int]] = None,
    has_substitute: Optional[str] = None,
    include_persons: bool = True
) -> Optional[str]:
    """Summarise the active export filters as names for the report footer.

    Args:
        user_ids: Active person filter.
        category_ids: Active category filter.
        has_substitute: 'yes', 'no', or None.
        include_persons: Omit the person filter when False (single-person
            exports already name the person in the title).

    Returns:
        Summary string, or None when no filter is active.
    """
    parts = []

    if include_persons and user_ids:
        names = db.session.scalars(
            select(User.name).where(User.id.in_(user_ids)).order_by(User.name)
        ).all()
        if names:
            parts.append('Personen: ' + ', '.join(names))

    if category_ids:
        names = db.session.scalars(
            select(Category.name).where(Category.id.in_(category_ids)).order_by(Category.name)
        ).all()
        if names:
            parts.append('Kategorien: ' + ', '.join(names))

    if has_substitute == 'yes':
        parts.append('Vertretung: mit')
    elif has_substitute == 'no':
        parts.append('Vertretung: ohne')

    if not parts:
        return None

    return ' · '.join(parts)


def get_absences_for_export(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    user_ids: Optional[List[int]] = None
) -> list[Absence]:
    """Get filtered absences for export.

    Args:
        from_date: Start date filter.
        to_date: End date filter.
        user_ids: Optional person filter (any of the given IDs).

    Returns:
        List of absences.
    """
    query = build_absence_query(
        from_date=from_date,
        to_date=to_date,
        user_ids=user_ids
    )
    return db.session.scalars(query).all()


def build_export_occurrences(
    from_date: date,
    to_date: date,
    user_ids: Optional[List[int]] = None,
    category_ids: Optional[List[int]] = None,
    has_substitute: Optional[str] = None
) -> list[dict]:
    """Load, expand and filter occurrences ready for rendering.

    Category and substitute filters apply to the effective occurrence
    state, not to the parent absences.

    Args:
        from_date: Start of the range.
        to_date: End of the range.
        user_ids: Optional person filter (any of the given IDs).
        category_ids: Optional effective-category filter (any of the given IDs).
        has_substitute: 'yes', 'no', or None.

    Returns:
        List of occurrence dicts sorted by date (and user name for ties).
    """
    from modules.absence.services import filter_occurrences

    absences = get_absences_for_export(
        from_date=from_date,
        to_date=to_date,
        user_ids=user_ids
    )

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, from_date, to_date
    )

    occurrences = filter_occurrences(
        occurrences,
        category_ids=category_ids,
        has_substitute=has_substitute
    )

    occurrences.sort(
        key=lambda o: (o['date'], o['user'].name if o.get('user') else '')
    )
    return occurrences
