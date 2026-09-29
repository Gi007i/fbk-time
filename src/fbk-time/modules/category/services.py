"""Category management services.

Provides business logic for category CRUD operations
with proper handling of absence relationships.
"""

from typing import Optional, Tuple

from sqlalchemy import delete, func, select, update

from core.db import db
from .models import Category
from modules.absence.models import Absence


def get_category_or_404(category_id: int) -> Category:
    """Get category by ID or abort with 404.

    Args:
        category_id: Category ID.

    Returns:
        Category instance.

    Raises:
        404: If category not found.
    """
    return db.get_or_404(Category, category_id)


def get_categories_list(
    status_filter: Optional[str] = None,
    page: int = 1,
    per_page: int = 0
) -> Tuple[list[Category], int]:
    """Get paginated list of categories with filters.

    Args:
        status_filter: Filter by state ('active', 'inactive' or 'all').
        page: Page number.
        per_page: Items per page (0 = all).

    Returns:
        Tuple of (categories, total_count).

    Raises:
        ValueError: If status_filter holds an unknown value.
    """
    query = select(Category)

    if status_filter == 'active':
        query = query.where(Category.active == True)
    elif status_filter == 'inactive':
        query = query.where(Category.active == False)
    elif status_filter and status_filter != 'all':
        raise ValueError(f'Unknown status filter: {status_filter}')

    total = db.session.scalar(
        select(func.count()).select_from(query.subquery())
    )
    query = query.order_by(Category.sort_order, Category.name)

    if per_page > 0:
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)
    categories = db.session.scalars(query).all()

    return categories, total


def create_category(
    name: str,
    color: str,
    text_color: str,
    icon: Optional[str] = None,
    requires_substitute: bool = False,
    is_present: bool = False,
    sort_order: int = 0,
    active: bool = True
) -> Tuple[Optional[Category], Optional[str]]:
    """Create a new category.

    Args:
        name: Category name (must be unique).
        color: Background color (#RRGGBB).
        text_color: Text color (#RRGGBB).
        icon: Optional icon identifier.
        requires_substitute: Whether absences require substitute.
        is_present: True = working remotely, False = absent.
        sort_order: Display order.
        active: Whether category is active.

    Returns:
        Tuple of (Category instance or None, error_message or None).
    """
    existing = db.session.scalars(
        select(Category).filter_by(name=name.strip())
    ).first()
    if existing:
        return None, 'Eine Kategorie mit diesem Namen existiert bereits.'

    category = Category(
        name=name.strip(),
        color=color.strip().upper(),
        text_color=text_color.strip().upper(),
        icon=icon.strip() if icon else None,
        requires_substitute=requires_substitute,
        is_present=is_present,
        sort_order=sort_order,
        active=active
    )

    db.session.add(category)
    return category, None


def update_category(
    category: Category,
    name: str,
    color: str,
    text_color: str,
    icon: Optional[str] = None,
    requires_substitute: bool = False,
    is_present: bool = False,
    sort_order: int = 0,
    active: bool = True
) -> Tuple[bool, Optional[str]]:
    """Update an existing category.

    Args:
        category: Category to update.
        name: New name (must be unique).
        color: New background color.
        text_color: New text color.
        icon: New icon identifier.
        requires_substitute: New substitute requirement.
        is_present: New presence status.
        sort_order: New display order.
        active: New active status.

    Returns:
        Tuple of (success, error_message).
    """
    existing = db.session.scalars(
        select(Category).where(
            Category.name == name.strip(),
            Category.id != category.id
        )
    ).first()

    if existing:
        return False, 'Eine Kategorie mit diesem Namen existiert bereits.'

    category.name = name.strip()
    category.color = color.strip().upper()
    category.text_color = text_color.strip().upper()
    category.icon = icon.strip() if icon else None
    category.requires_substitute = requires_substitute
    category.is_present = is_present
    category.sort_order = sort_order
    category.active = active

    return True, None


def get_absence_count(category_id: int) -> int:
    """Get count of absences using this category.

    Args:
        category_id: Category ID.

    Returns:
        Number of absences using this category.
    """
    return db.session.scalar(
        select(func.count()).select_from(Absence).filter_by(category_id=category_id)
    )


def delete_category_with_absences(category: Category) -> str:
    """Delete category and all its absences.

    Args:
        category: Category to delete.

    Returns:
        Success message.
    """
    from modules.absence.models import RecurrenceException
    from modules.absence.history import track_occurrence_category_transfer

    absences_count = get_absence_count(category.id)
    name = category.name

    if absences_count > 0:
        db.session.execute(
            delete(Absence).filter_by(category_id=category.id)
        )

    # Overrides on series of *other* categories survive the delete above. The
    # FK clears their category id but leaves the override flag set, which
    # would render an occurrence without an effective category. Dropping the
    # override lets it fall back to the category of its series.
    orphaned_overrides = db.session.scalars(
        select(RecurrenceException).where(
            RecurrenceException.modified_category_id == category.id
        )
    ).all()
    for override in orphaned_overrides:
        track_occurrence_category_transfer(
            override.absence_id,
            override.exception_date,
            name,
            override.absence.category.name
        )
        override.modified_category_id = None
        override.modified_category_overridden = False

    db.session.delete(category)

    if absences_count > 0:
        return f'Kategorie "{name}" und {absences_count} Abwesenheit(en) wurden gelöscht.'
    return f'Kategorie "{name}" wurde gelöscht.'


def transfer_absences_and_delete(
    category: Category,
    target_category_id: int
) -> Tuple[bool, str]:
    """Transfer absences to another category and delete this one.

    Args:
        category: Category to delete.
        target_category_id: ID of category to receive absences.

    Returns:
        Tuple of (success, message).
    """
    if target_category_id == category.id:
        return False, 'Zielkategorie kann nicht die gleiche Kategorie sein.'

    target_category = db.session.get(Category, target_category_id)
    if not target_category:
        return False, 'Zielkategorie nicht gefunden.'

    from modules.absence.models import RecurrenceException
    from modules.absence.history import (
        track_category_transfer,
        track_occurrence_category_transfer,
    )

    old_name = category.name
    new_name = target_category.name

    affected_ids = db.session.scalars(
        select(Absence.id).filter_by(category_id=category.id)
    ).all()
    absences_count = len(affected_ids)
    for absence_id in affected_ids:
        track_category_transfer(absence_id, old_name, new_name)
    db.session.execute(
        update(Absence)
        .filter_by(category_id=category.id)
        .values(category_id=target_category_id)
    )

    # Occurrence overrides must follow the transfer; FK SET NULL would
    # strip the category from a modified occurrence (category is mandatory).
    affected_exceptions = db.session.scalars(
        select(RecurrenceException).where(
            RecurrenceException.modified_category_id == category.id
        )
    ).all()
    for exception in affected_exceptions:
        track_occurrence_category_transfer(
            exception.absence_id, exception.exception_date, old_name, new_name
        )
        exception.modified_category_id = target_category_id

    db.session.delete(category)

    message = (
        f'{absences_count} Abwesenheit(en) nach "{new_name}" übertragen, '
        f'Kategorie "{old_name}" gelöscht.'
    )
    return True, message


def toggle_category_active(category: Category) -> str:
    """Toggle category active status.

    Args:
        category: Category to toggle.

    Returns:
        Status message.
    """
    category.active = not category.active
    status = 'aktiviert' if category.active else 'deaktiviert'
    return f'Kategorie "{category.name}" wurde {status}.'


def get_categories_excluding(exclude_id: int) -> list[Category]:
    """Get all categories except the specified one.

    Args:
        exclude_id: Category ID to exclude.

    Returns:
        List of categories ordered by name.
    """
    return db.session.scalars(
        select(Category)
        .where(Category.id != exclude_id)
        .order_by(Category.name)
    ).all()


