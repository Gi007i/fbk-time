"""Profile services.

Provides business logic for user profile data retrieval.
"""

from flask_login import current_user


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
