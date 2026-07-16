"""User management services.

Provides business logic for user creation, status management,
and role-based access control validation.
"""

from typing import Optional, Tuple
import secrets

from core.extensions import db
from core.settings_manager import settings_manager
from modules.auth.services import hash_password
from modules.auth.models import User, UserRole, UserStatus, LoginAttempt


def get_user_or_404(user_id: int) -> User:
    """Get user by ID or abort with 404.

    Args:
        user_id: User ID.

    Returns:
        User instance.

    Raises:
        404: If user not found.
    """
    return User.query.get_or_404(user_id)


def create_user(
    username: str,
    name: str,
    password: Optional[str] = None,
    email: Optional[str] = None,
    role: UserRole = UserRole.USER,
    as_managed: bool = False
) -> User:
    """Create a new user with proper defaults from settings.

    Args:
        username: Unique username (will be normalized to lowercase).
        name: Display name.
        password: Password (required unless as_managed=True).
        email: Optional email address (will be normalized to lowercase).
        role: User role (default: USER).
        as_managed: If True, create as MANAGED status without real password.

    Returns:
        Created User instance.
    """
    if as_managed:
        password_hash = hash_password(secrets.token_hex(32))
        status = UserStatus.MANAGED
        force_pwd_change = False
        has_real_pwd = False
    else:
        password_hash = hash_password(password)
        status = UserStatus.ACTIVE
        force_pwd_change = settings_manager.get('password_force_change_on_first_login')
        has_real_pwd = True

    user = User(
        username=username.strip().lower(),
        password_hash=password_hash,
        name=name.strip(),
        email=email.strip().lower() if email else None,
        role=role,
        status=status,
        force_password_change=force_pwd_change,
        has_real_password=has_real_pwd,
        theme=settings_manager.get('user_default_theme'),
        date_format=settings_manager.get('user_default_date_format'),
        items_per_page=settings_manager.get('user_default_items_per_page'),
        holiday_region=settings_manager.get('user_default_holiday_region'),
        default_text_color=settings_manager.get('user_default_text_color')
    )

    db.session.add(user)
    return user


def validate_last_admin(user: User, new_role: Optional[UserRole] = None) -> Tuple[bool, Optional[str]]:
    """Validate that changing user's role won't remove the last admin.

    Args:
        user: User being modified.
        new_role: Proposed new role (None = no role change).

    Returns:
        Tuple of (is_valid, error_message).
    """
    if user.role != UserRole.ADMIN:
        return True, None

    if new_role is None or new_role == UserRole.ADMIN:
        return True, None

    admin_count = User.query.filter_by(
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE
    ).count()

    if admin_count <= 1:
        return False, 'Der letzte aktive Admin kann seine Rolle nicht ändern.'

    return True, None


def can_toggle_user_status(current_user: User, target_user: User) -> Tuple[bool, Optional[str]]:
    """Check if current user can toggle target user's status.

    Args:
        current_user: User performing the action.
        target_user: User whose status is being toggled.

    Returns:
        Tuple of (can_toggle, error_message).
    """
    if target_user.id == current_user.id:
        return False, 'Sie können Ihren eigenen Status nicht ändern.'

    if not current_user.is_admin and target_user.role != UserRole.USER:
        return False, 'Zugriff verweigert.'

    return True, None


def toggle_user_status(user: User, current_user: User) -> Tuple[UserStatus, str]:
    """Toggle user status between ACTIVE, DISABLED, LOCKED.

    Args:
        user: User to toggle.
        current_user: User performing the action.

    Returns:
        Tuple of (new_status, message).

    Raises:
        ValueError: If toggling would remove the last active admin.
    """
    if user.status == UserStatus.ACTIVE:
        # Re-check admin count before deactivating to reduce TOCTOU window
        if user.role == UserRole.ADMIN:
            admin_count = User.query.filter_by(
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE
            ).count()
            if admin_count <= 1:
                raise ValueError('Der letzte aktive Admin kann nicht deaktiviert werden.')

        user.status = UserStatus.DISABLED
        message = f'Benutzer "{user.name}" wurde deaktiviert.'

    elif user.status == UserStatus.DISABLED:
        user.status = UserStatus.ACTIVE
        message = f'Benutzer "{user.name}" wurde aktiviert.'

    elif user.status == UserStatus.LOCKED:
        user.status = UserStatus.ACTIVE
        LoginAttempt.query.filter_by(identifier=user.username).delete()
        message = f'Benutzer "{user.name}" wurde entsperrt.'

    elif user.status == UserStatus.PENDING:
        user.status = UserStatus.ACTIVE
        message = f'Benutzer "{user.name}" wurde aktiviert.'

    else:
        message = f'Status von "{user.name}" konnte nicht geändert werden.'

    return user.status, message


def activate_login_for_managed_user(user: User, password: str) -> str:
    """Activate login for a MANAGED user by setting password.

    Args:
        user: MANAGED user to activate.
        password: New password to set.

    Returns:
        Success message.
    """
    user.password_hash = hash_password(password)
    user.status = UserStatus.ACTIVE
    user.has_real_password = True
    user.force_password_change = True

    return f'Login für "{user.name}" wurde aktiviert.'


def activate_login_with_existing_password(user: User) -> str:
    """Activate login for a MANAGED user who has a real password.

    Args:
        user: MANAGED user to activate (must have has_real_password=True).

    Returns:
        Success message.
    """
    user.status = UserStatus.ACTIVE
    user.force_password_change = True

    return f'Login für "{user.name}" wurde aktiviert.'


def can_change_password(current_user: User, target_user: User) -> Tuple[bool, Optional[str]]:
    """Check if current user can change target user's password.

    Args:
        current_user: User performing the action.
        target_user: User whose password is being changed.

    Returns:
        Tuple of (can_change, error_message).
    """
    if target_user.status == UserStatus.MANAGED:
        return False, 'Passwort kann für MANAGED User nicht geändert werden. Erst Login aktivieren.'

    if not current_user.is_admin and target_user.role != UserRole.USER:
        return False, 'Zugriff verweigert.'

    return True, None


def set_user_password(user: User, password: str, by_admin: bool = False) -> None:
    """Set user password.

    Args:
        user: User to update.
        password: New password.
        by_admin: If True, force password change on next login.
    """
    user.password_hash = hash_password(password)
    user.has_real_password = True
    user.credential_version += 1

    if by_admin:
        user.force_password_change = True


def get_users_list(
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    role_filter: Optional[str] = None,
    page: int = 1,
    per_page: int = 0
) -> Tuple[list[User], int]:
    """Get paginated list of users with filters.

    Args:
        search: Search in name/username/email.
        status_filter: Filter by status ('active', 'all', or status value).
        role_filter: Filter by role ('all' or role value).
        page: Page number.
        per_page: Items per page (0 = all).

    Returns:
        Tuple of (users, total_count).
    """
    query = User.query

    if search:
        query = query.filter(
            db.or_(
                User.name.icontains(search, autoescape=True),
                User.username.icontains(search, autoescape=True),
                User.email.icontains(search, autoescape=True)
            )
        )

    if status_filter == 'active':
        query = query.filter(User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]))
    elif status_filter and status_filter != 'all':
        query = query.filter(User.status == UserStatus(status_filter))

    if role_filter and role_filter != 'all':
        query = query.filter(User.role == UserRole(role_filter))

    total = query.count()
    query = query.order_by(User.name)

    if per_page > 0:
        offset = (page - 1) * per_page
        users = query.offset(offset).limit(per_page).all()
    else:
        users = query.all()

    return users, total


def get_user_absence_count(user_id: int) -> int:
    """Get count of absences for a user.

    Args:
        user_id: User ID.

    Returns:
        Number of absence records.
    """
    from modules.absence.models import Absence
    return Absence.query.filter_by(user_id=user_id).count()


def username_exists(username: str) -> bool:
    """Check if a username already exists.

    Args:
        username: Username to check (will be normalized to lowercase).

    Returns:
        True if username exists, False otherwise.
    """
    return User.query.filter_by(username=username.strip().lower()).first() is not None


def email_exists(email: str, exclude_user_id: int | None = None) -> bool:
    """Check if an email already exists.

    Args:
        email: Email to check (will be normalized to lowercase).
        exclude_user_id: User ID to exclude from check (for edit forms).

    Returns:
        True if email exists (for another user), False otherwise.
    """
    normalized_email = email.strip().lower()
    existing = User.query.filter_by(email=normalized_email).first()

    if not existing:
        return False

    if exclude_user_id and existing.id == exclude_user_id:
        return False

    return True
