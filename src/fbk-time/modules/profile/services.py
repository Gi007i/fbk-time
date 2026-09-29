"""Profile services.

Provides business logic for user profile data retrieval and self-service updates.
"""

from typing import Optional

from core.auth import current_user
from core.db import db
from modules.auth.models import User
from utils.validators import normalize_email


def get_profile_data() -> dict:
    """Get current user's profile data.

    Returns:
        Dict with user profile information.
    """
    return {
        'id': current_user.id,
        'username': current_user.username,
        'name': current_user.name,
        'email': current_user.email,
        'role': current_user.role,
        'status': current_user.status,
        'theme': current_user.theme,
        'date_format': current_user.date_format,
        'items_per_page': current_user.items_per_page,
        'holiday_region': current_user.holiday_region,
        'created_at': current_user.created_at,
        'previous_login_at': current_user.previous_login_at,
        'force_password_change': current_user.force_password_change
    }


def changes_email(user: User, email: Optional[str]) -> bool:
    """Report whether a submitted email differs from the stored one.

    Args:
        user: User whose profile is edited.
        email: Submitted email address (None or empty = no email).

    Returns:
        True if saving would change the email address.
    """
    return normalize_email(email) != user.email


def update_profile(user: User, name: str, email: Optional[str]) -> None:
    """Update the display name and email of a user.

    Args:
        user: User instance to update.
        name: New display name.
        email: New email address (None or empty = clear email).
    """
    user.name = name.strip()
    user.email = normalize_email(email)
    db.session.commit()

