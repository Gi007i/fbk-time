"""Profile services.

Provides business logic for user profile data retrieval and self-service updates.
"""

from typing import Optional

from flask_login import current_user

from core.extensions import db
from modules.auth.models import User


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
        'last_login': current_user.last_login,
        'force_password_change': current_user.force_password_change
    }


def update_profile(user: User, name: str, email: Optional[str]) -> None:
    """Update the display name and email of a user.

    Args:
        user: User instance to update.
        name: New display name.
        email: New email address (None or empty = clear email).
    """
    user.name = name.strip()
    user.email = email.strip().lower() if email else None
    db.session.commit()

