"""User management services.

Provides business logic for user creation, status management,
and role-based access control validation.
"""

from typing import Optional, Tuple
import secrets

from sqlalchemy import func, or_, select, update

from core.db import db
from core.settings_manager import settings_manager
from modules.auth.services import clear_login_attempts_for_username, hash_password
from modules.auth.models import User, UserRole, UserStatus, new_credential_version
from modules.absence.models import Absence
from utils.validators import normalize_email, normalize_username


def get_user_or_404(user_id: int) -> User:
    """Get user by ID or abort with 404.

    Args:
        user_id: User ID.

    Returns:
        User instance.

    Raises:
        404: If user not found.
    """
    return db.get_or_404(User, user_id)


def delete_user(user: User) -> None:
    """Delete a user and detach them as substitute everywhere.

    Substitute references are cleared explicitly rather than via FK SET
    NULL: this bumps updated_at for iCal exports and records an audit
    entry on each affected absence.

    Args:
        user: User to delete.
    """
    from modules.absence.models import RecurrenceException
    from modules.absence.history import (
        track_substitute_cleared_on_user_delete,
        track_occurrence_substitute_cleared,
    )

    affected_ids = db.session.scalars(
        select(Absence.id).where(Absence.substitute_id == user.id)
    ).all()
    for absence_id in affected_ids:
        track_substitute_cleared_on_user_delete(absence_id)
    db.session.execute(
        update(Absence)
        .where(Absence.substitute_id == user.id)
        .values(substitute_id=None)
    )

    affected_exceptions = db.session.scalars(
        select(RecurrenceException).where(
            RecurrenceException.modified_substitute_id == user.id
        )
    ).all()
    for exception in affected_exceptions:
        track_occurrence_substitute_cleared(
            exception.absence_id, exception.exception_date
        )
        exception.modified_substitute_id = None

    db.session.delete(user)
    db.session.commit()


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
        username=normalize_username(username),
        password_hash=password_hash,
        name=name.strip(),
        email=normalize_email(email),
        role=role,
        status=status,
        force_password_change=force_pwd_change,
        has_real_password=has_real_pwd,
        theme=settings_manager.get('user_default_theme'),
        date_format=settings_manager.get('user_default_date_format'),
        items_per_page=settings_manager.get('user_default_items_per_page'),
        holiday_region=settings_manager.get('user_default_holiday_region'),
        default_text_color=settings_manager.get('user_default_text_color'),
        start_page=settings_manager.get('user_default_start_page'),
        view_scope=settings_manager.get('user_default_view_scope')
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
    if user.role != UserRole.ADMIN or user.status != UserStatus.ACTIVE:
        return True, None

    if new_role is None or new_role == UserRole.ADMIN:
        return True, None

    if _active_admin_count() <= 1:
        return False, 'Der letzte aktive Admin kann seine Rolle nicht ändern.'

    return True, None


def _active_admin_count() -> int:
    return db.session.scalar(
        select(func.count()).select_from(User).filter_by(
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE
        )
    )


def validate_status_change(
    current_user: User,
    user: User,
    new_status: UserStatus,
    new_role: UserRole
) -> Tuple[bool, Optional[str]]:
    """Validate the role and status submitted through the user edit form.

    Mirrors the status toggle guards so the edit form is no unguarded
    second path; the MANAGED check runs on the resulting role/status pair.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if new_status == UserStatus.MANAGED and new_role in (UserRole.ADMIN, UserRole.MANAGER):
        return False, 'Admin und Manager können nicht auf MANAGED gesetzt werden.'

    if new_status == user.status:
        return True, None

    if user.id == current_user.id:
        return False, 'Sie können Ihren eigenen Status nicht ändern.'

    if (user.role == UserRole.ADMIN
            and user.status == UserStatus.ACTIVE
            and new_status != UserStatus.ACTIVE
            and _active_admin_count() <= 1):
        return False, 'Der letzte aktive Admin kann nicht deaktiviert werden.'

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


def toggle_user_status(user: User) -> Tuple[UserStatus, str]:
    """Toggle ACTIVE to DISABLED, or DISABLED, LOCKED and PENDING to ACTIVE.

    Args:
        user: User to toggle.

    Returns:
        Tuple of (new_status, message).

    Raises:
        ValueError: If toggling would remove the last active admin.
    """
    if user.status == UserStatus.ACTIVE:
        if user.role == UserRole.ADMIN and _active_admin_count() <= 1:
            raise ValueError('Der letzte aktive Admin kann nicht deaktiviert werden.')

        user.status = UserStatus.DISABLED
        message = f'Benutzer "{user.name}" wurde deaktiviert.'

    elif user.status == UserStatus.DISABLED:
        user.status = UserStatus.ACTIVE
        message = f'Benutzer "{user.name}" wurde aktiviert.'

    elif user.status == UserStatus.LOCKED:
        user.status = UserStatus.ACTIVE
        clear_login_attempts_for_username(user.username)
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
    set_user_password(user, password, require_change=True)
    user.status = UserStatus.ACTIVE

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
    # The self-service form confirms the current password and keeps this
    # session alive; this path does neither.
    if target_user.id == current_user.id:
        return False, 'Ihr eigenes Passwort ändern Sie unter Mein Profil.'

    if target_user.status == UserStatus.MANAGED:
        return False, 'Passwort kann für MANAGED User nicht geändert werden. Erst Login aktivieren.'

    if not current_user.is_admin and target_user.role != UserRole.USER:
        return False, 'Zugriff verweigert.'

    return True, None


def set_user_password(user: User, password: str, require_change: bool) -> None:
    """Set a user's password from any entry point (web, CLI, self-service).

    The single place that writes password state, so callers cannot drift
    apart on which flags they update.

    Args:
        user: User to update.
        password: New plain-text password.
        require_change: Whether the user must choose a new password at next
            login; True when someone else sets it, False for self-service.
    """
    user.password_hash = hash_password(password)
    user.has_real_password = True
    user.force_password_change = require_change
    # Invalidates every existing session and remember-me cookie of this user
    # on all other devices (see User.get_id).
    user.credential_version = new_credential_version()


def can_end_user_sessions(current_user: User, target_user: User) -> Tuple[bool, Optional[str]]:
    """Check if current user may end all sessions of target user.

    Args:
        current_user: User performing the action.
        target_user: User whose sessions are ended.

    Returns:
        Tuple of (allowed, error_message).
    """
    if target_user.id == current_user.id:
        return False, 'Eigene Sitzungen beenden Sie unter Mein Profil.'

    if target_user.status != UserStatus.ACTIVE:
        return False, 'Nur aktive Konten haben Sitzungen.'

    if not current_user.is_admin and target_user.role != UserRole.USER:
        return False, 'Zugriff verweigert.'

    return True, None


def end_user_sessions(user: User) -> None:
    """Invalidate every session and remember-me cookie of a user.

    Same mechanism as a password change (see User.get_id). The caller
    commits and, for the own account, re-establishes the current session.

    Args:
        user: User whose sessions are ended.
    """
    user.credential_version = new_credential_version()


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
    query = select(User)

    if search:
        query = query.where(
            or_(
                User.name.icontains(search, autoescape=True),
                User.username.icontains(search, autoescape=True),
                User.email.icontains(search, autoescape=True)
            )
        )

    if status_filter == 'active':
        query = query.where(User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]))
    elif status_filter and status_filter != 'all':
        query = query.where(User.status == UserStatus(status_filter))

    if role_filter and role_filter != 'all':
        query = query.where(User.role == UserRole(role_filter))

    total = db.session.scalar(
        select(func.count()).select_from(query.subquery())
    )
    query = query.order_by(User.name)

    if per_page > 0:
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)
    users = db.session.scalars(query).all()

    return users, total


def username_exists(username: str) -> bool:
    """Check if a username already exists.

    Args:
        username: Username to check (will be normalized to lowercase).

    Returns:
        True if username exists, False otherwise.
    """
    return db.session.scalars(
        select(User).filter_by(username=normalize_username(username))
    ).first() is not None


def email_exists(email: str, exclude_user_id: int | None = None) -> bool:
    """Check if an email already exists.

    Args:
        email: Email to check (will be normalized to lowercase).
        exclude_user_id: User ID to exclude from check (for edit forms).

    Returns:
        True if email exists (for another user), False otherwise.
    """
    existing = db.session.scalars(
        select(User).filter_by(email=normalize_email(email))
    ).first()

    if not existing:
        return False

    if exclude_user_id and existing.id == exclude_user_id:
        return False

    return True
