"""Category management services.

Provides business logic for category CRUD operations
with proper handling of absence relationships.
"""

from typing import Optional, Tuple

from core.extensions import db
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
    return Category.query.get_or_404(category_id)


def get_categories_list(
    show_inactive: bool = False,
    page: int = 1,
    per_page: int = 0
) -> Tuple[list[Category], int]:
    """Get paginated list of categories.

    Args:
        show_inactive: Include inactive categories.
        page: Page number.
        per_page: Items per page (0 = all).

    Returns:
        Tuple of (categories, total_count).
    """
    query = Category.query
    if not show_inactive:
        query = query.filter(Category.active == True)

    total = query.count()
    query = query.order_by(Category.sort_order, Category.name)

    if per_page > 0:
        offset = (page - 1) * per_page
        categories = query.offset(offset).limit(per_page).all()
    else:
        categories = query.all()

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
    existing = Category.query.filter_by(name=name.strip()).first()
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
    existing = Category.query.filter(
        Category.name == name.strip(),
        Category.id != category.id
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
    return Absence.query.filter_by(category_id=category_id).count()


def delete_category_with_absences(category: Category) -> str:
    """Delete category and all its absences.

    Args:
        category: Category to delete.

    Returns:
        Success message.
    """
    absences_count = Absence.query.filter_by(category_id=category.id).count()

    if absences_count > 0:
        Absence.query.filter_by(category_id=category.id).delete()

    name = category.name
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

    absences_count = Absence.query.filter_by(category_id=category.id).count()
    Absence.query.filter_by(category_id=category.id).update(
        {'category_id': target_category_id}
    )

    name = category.name
    db.session.delete(category)

    message = (
        f'{absences_count} Abwesenheit(en) nach "{target_category.name}" übertragen, '
        f'Kategorie "{name}" gelöscht.'
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
    return Category.query.filter(
        Category.id != exclude_id
    ).order_by(Category.name).all()


def get_all_categories_ordered() -> list[Category]:
    """Get all categories ordered by sort_order.

    Returns:
        List of all categories.
    """
    return Category.query.order_by(Category.sort_order).all()


def add_absence_counts_to_categories(categories: list[Category]) -> None:
    """Add absence_count attribute to each category.

    Args:
        categories: List of Category instances to annotate.
    """
    for cat in categories:
        cat.absence_count = cat.absences.count()
