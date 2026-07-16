"""Authentication services.

Provides Flask-Login integration, Argon2id password verification,
and account lockout protection with RBAC status checks.
"""

from datetime import datetime, timezone, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from flask import session
from flask_login import login_user, logout_user

from config import Config
from core.extensions import login_manager, db
from core.settings_manager import settings_manager
from .models import User, UserStatus, UserRole, LoginAttempt


ph = PasswordHasher(
    time_cost=Config.ARGON2_TIME_COST,
    memory_cost=Config.ARGON2_MEMORY_COST,
    parallelism=Config.ARGON2_PARALLELISM,
    hash_len=Config.ARGON2_HASH_LENGTH,
    salt_len=Config.ARGON2_SALT_LENGTH
)

# Pre-computed for timing-attack prevention
DUMMY_HASH = ph.hash("timing_attack_prevention_dummy")


def _get_lockout_threshold():
    """Get lockout threshold from settings."""
    return settings_manager.get('lockout_threshold')


def _get_lockout_duration():
    """Get lockout duration as timedelta from settings."""
    return timedelta(minutes=settings_manager.get('lockout_duration_minutes'))


def _get_delay_enabled():
    """Get whether delay is enabled from settings."""
    return settings_manager.get('lockout_delay_enabled')


def _get_delay_base_seconds():
    """Get delay base seconds from settings."""
    return settings_manager.get('lockout_delay_base_seconds')


def _get_delay_max_seconds():
    """Get delay max seconds from settings."""
    return settings_manager.get('lockout_delay_max_seconds')


def hash_password(password):
    """Hash a password using Argon2id.

    Args:
        password: Plain text password.

    Returns:
        Hashed password string.
    """
    return ph.hash(password)


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login session management.

    The stored identity is ``id:credential_version`` (see User.get_id).
    A user is loaded only when ACTIVE and the version still matches, so a
    password change invalidates every existing session and remember-me
    cookie.

    Args:
        user_id: Versioned identity from the session or remember cookie.

    Returns:
        User instance or None if not found, not active, or stale.
    """
    raw_id, _, version = str(user_id).partition(':')
    try:
        user = db.session.get(User, int(raw_id))
    except ValueError:
        return None
    if not user or user.status != UserStatus.ACTIVE:
        return None
    if version != str(user.credential_version):
        return None
    return user


def authenticate_user(username, password):
    """Authenticate user with constant-time behavior and status check.

    Prevents user enumeration via timing analysis by always performing
    a hash verification, even when the user doesn't exist.

    Args:
        username: Username to authenticate.
        password: Password to verify.

    Returns:
        Tuple of (User instance or None, error_message or None).
        User is returned only if authentication successful and status is ACTIVE.
    """
    user = User.query.filter_by(username=username).first()

    # Always perform hash verification to prevent timing attacks
    hash_to_verify = user.password_hash if user else DUMMY_HASH

    try:
        ph.verify(hash_to_verify, password)
        password_valid = True

        # Rehash if parameters have changed (only for real users)
        if user and ph.check_needs_rehash(user.password_hash):
            user.password_hash = ph.hash(password)
            db.session.commit()

    except (VerifyMismatchError, InvalidHashError):
        password_valid = False

    if not user or not password_valid:
        return None, None

    # Check user status - generic message to prevent user enumeration
    if user.status != UserStatus.ACTIVE:
        return None, 'Anmeldung fehlgeschlagen.'

    # SingleUser mode: Only admin/manager can login
    if (settings_manager.get('operation_mode') == 'single_user'
            and user.role == UserRole.USER):
        return None, 'Anmeldung fehlgeschlagen.'

    return user, None


def initialize_session(user):
    """Populate session metadata for an authenticated user.

    Sets the keys consumed by the session-lifecycle and role-validation
    hooks: persistence flag, creation timestamp, last-activity marker,
    role identifier, and (for USER role) the current session version. Used
    by login_user_session, change-password regeneration, and remember-me
    cookie restoration.

    Both ``_created_at`` (absolute lifetime) and ``_last_activity`` (idle
    timeout) are set to the current time. On remember-me cookie restoration
    this resets both timers, so the effective sign-in ceiling is the
    remember cookie's duration, not ``PERMANENT_SESSION_LIFETIME``.

    Args:
        user: Authenticated user instance.
    """
    session.permanent = True
    now = datetime.now(timezone.utc).isoformat()
    session['_created_at'] = now
    session['_last_activity'] = now
    session['user_role'] = user.role.value
    if user.role == UserRole.USER:
        session['_session_version'] = settings_manager.get('user_session_version')


def login_user_session(user, remember=False):
    """Log in a user with session fixation prevention.

    Regenerates session ID before login to prevent session fixation attacks.
    Shifts last_login_at into previous_login_at, then records the new timestamp.

    Args:
        user: User instance to log in.
        remember: If True, create persistent session.

    Returns:
        True if login successful.
    """
    # Regenerate session ID to prevent session fixation
    session.clear()

    # Shift current login into previous before recording the new one
    user.previous_login_at = user.last_login_at
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()

    login_user(user, remember=remember)
    initialize_session(user)

    return True


def logout_user_session():
    """Log out the current user."""
    logout_user()
    # Preserve flask_login's _remember flag so the after_request handler
    # can still delete the remember cookie after we wipe the session.
    remember_flag = session.get('_remember')
    session.clear()
    if remember_flag is not None:
        session['_remember'] = remember_flag


def calculate_login_delay(identifier):
    """Calculate delay in seconds before next login attempt is allowed.

    Uses exponential backoff: base_seconds * 2^(attempts-1), capped at max_seconds.
    Returns 0 if delay is disabled or no failed attempts.

    Args:
        identifier: Username or IP address.

    Returns:
        Delay in seconds (0 if no delay required).
    """
    if not _get_delay_enabled():
        return 0

    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()
    if not attempt or attempt.attempt_count == 0:
        return 0

    delay = _get_delay_base_seconds() * (2 ** (attempt.attempt_count - 1))
    return min(delay, _get_delay_max_seconds())


def is_account_locked(identifier):
    """Check if account is locked due to failed login attempts.

    Args:
        identifier: Username or IP address to check.

    Returns:
        True if account is locked, False otherwise.
    """
    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()

    if not attempt or not attempt.locked_until:
        return False

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc >= attempt.locked_until:
        db.session.delete(attempt)
        db.session.commit()
        return False

    return True


def is_login_throttled(identifier):
    """Check if login attempt is throttled by lockout or progressive delay.

    Combines lockout and delay checks into a single timestamp-based check.
    Returns remaining wait time instead of blocking the worker thread.

    Args:
        identifier: Username to check.

    Returns:
        Remaining seconds to wait, or 0 if login attempt is allowed.
    """
    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()
    if not attempt:
        return 0

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if attempt.locked_until:
        if now_utc < attempt.locked_until:
            return int((attempt.locked_until - now_utc).total_seconds()) + 1
        db.session.delete(attempt)
        db.session.commit()
        return 0

    if not _get_delay_enabled() or attempt.attempt_count == 0:
        return 0

    if attempt.last_attempt:
        delay = _get_delay_base_seconds() * (2 ** (attempt.attempt_count - 1))
        delay = min(delay, _get_delay_max_seconds())
        next_allowed = attempt.last_attempt + timedelta(seconds=delay)
        if now_utc < next_allowed:
            return int((next_allowed - now_utc).total_seconds()) + 1

    return 0


def record_failed_attempt(identifier):
    """Record a failed login attempt and lock account if threshold reached.

    Args:
        identifier: Username or IP address.

    Returns:
        Dict with attempt count and locked_until if locked.
    """
    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()

    if not attempt:
        attempt = LoginAttempt(identifier=identifier, attempt_count=0)
        db.session.add(attempt)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    attempt.attempt_count += 1
    attempt.last_attempt = now_utc

    if attempt.attempt_count >= _get_lockout_threshold():
        attempt.locked_until = now_utc + _get_lockout_duration()

    db.session.commit()

    return {
        'count': attempt.attempt_count,
        'locked_until': attempt.locked_until
    }


def clear_failed_attempts(identifier):
    """Clear failed attempts after successful login.

    Args:
        identifier: Username or IP address.
    """
    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()
    if attempt:
        db.session.delete(attempt)
        db.session.commit()


def clear_failed_ip_attempts(ip_address):
    """Clear an IP address's failed-attempt record after a successful login.

    Without this the IP counter only ever grows, so occasional mistypes by
    different users behind one NAT could lock the whole IP.
    """
    clear_failed_attempts(_get_ip_identifier(ip_address))


_IP_LOCKOUT_MULTIPLIER = 5


def _get_ip_identifier(ip_address):
    return f'ip:{ip_address}'


def is_ip_throttled(ip_address):
    """Check if an IP address is locked out due to excessive failed attempts.

    Defense-in-depth measure. Primary IP rate limiting is handled by Nginx.
    Uses a higher threshold (lockout_threshold * 5) to avoid locking
    legitimate users on shared IPs while still catching credential stuffing.

    Args:
        ip_address: Client IP address.

    Returns:
        Remaining seconds to wait, or 0 if allowed.
    """
    identifier = _get_ip_identifier(ip_address)
    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()
    if not attempt:
        return 0

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if attempt.locked_until:
        if now_utc < attempt.locked_until:
            return int((attempt.locked_until - now_utc).total_seconds()) + 1
        db.session.delete(attempt)
        db.session.commit()

    return 0


def record_failed_ip_attempt(ip_address):
    """Record a failed login attempt for an IP address.

    Args:
        ip_address: Client IP address.
    """
    identifier = _get_ip_identifier(ip_address)
    attempt = LoginAttempt.query.filter_by(identifier=identifier).first()

    if not attempt:
        attempt = LoginAttempt(identifier=identifier, attempt_count=0)
        db.session.add(attempt)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    attempt.attempt_count += 1
    attempt.last_attempt = now_utc

    ip_threshold = _get_lockout_threshold() * _IP_LOCKOUT_MULTIPLIER
    if attempt.attempt_count >= ip_threshold:
        attempt.locked_until = now_utc + _get_lockout_duration()

    db.session.commit()


def cleanup_expired_lockouts():
    """Remove expired lockout records and stale attempts from the database.

    Deletes:
    1. Entries with expired lockout (locked_until < now)
    2. Entries without lockout older than attempt_retention_hours

    Returns:
        Number of records removed.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    retention_hours = settings_manager.get('lockout_attempt_retention_hours')
    retention_cutoff = now_utc - timedelta(hours=retention_hours)

    expired_lockouts = LoginAttempt.query.filter(
        LoginAttempt.locked_until.isnot(None),
        LoginAttempt.locked_until < now_utc
    ).all()

    stale_attempts = LoginAttempt.query.filter(
        LoginAttempt.locked_until.is_(None),
        LoginAttempt.last_attempt < retention_cutoff
    ).all()

    count = 0
    for attempt in expired_lockouts + stale_attempts:
        db.session.delete(attempt)
        count += 1

    if count > 0:
        db.session.commit()

    return count


def deactivate_inactive_accounts():
    """Disable accounts that have been inactive for configured period.

    Accounts are considered inactive if:
    1. last_login_at is older than inactive_account_days, OR
    2. last_login_at is NULL and created_at is older than inactive_account_days

    Only ACTIVE accounts are affected. Admin accounts are excluded.

    Returns:
        Number of accounts disabled.
    """
    if not settings_manager.get('inactive_account_auto_disable'):
        return 0

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    inactive_days = settings_manager.get('inactive_account_days')
    inactive_cutoff = now_utc - timedelta(days=inactive_days)

    from .models import UserRole

    inactive_users = User.query.filter(
        User.status == UserStatus.ACTIVE,
        User.role != UserRole.ADMIN,
        db.or_(
            db.and_(
                User.last_login_at.isnot(None),
                User.last_login_at < inactive_cutoff
            ),
            db.and_(
                User.last_login_at.is_(None),
                User.created_at < inactive_cutoff
            )
        )
    ).all()

    count = 0
    for user in inactive_users:
        user.status = UserStatus.DISABLED
        count += 1

    if count > 0:
        db.session.commit()

    return count


def get_lockout_status_for_users(usernames: list[str]) -> dict[str, dict]:
    """Get lockout status for multiple users.

    Args:
        usernames: List of usernames to check.

    Returns:
        Dict mapping username to lockout info (attempt_count, locked_until).
    """
    if not usernames:
        return {}

    attempts = LoginAttempt.query.filter(
        LoginAttempt.identifier.in_(usernames)
    ).all()

    return {
        attempt.identifier: {
            'attempt_count': attempt.attempt_count,
            'locked_until': attempt.locked_until
        }
        for attempt in attempts
    }


def register_pending_user(
    username: str,
    name: str,
    password: str,
    email: str | None = None
) -> User:
    """Register a new user with PENDING status (self-registration).

    Args:
        username: Username (will be normalized to lowercase).
        name: Display name.
        password: Plain text password (will be hashed).
        email: Optional email address (will be normalized to lowercase).

    Returns:
        Created User instance.
    """
    user = User(
        username=username.strip().lower(),
        name=name.strip(),
        email=email.strip().lower() if email else None,
        password_hash=hash_password(password),
        status=UserStatus.PENDING,
        theme=settings_manager.get('user_default_theme'),
        date_format=settings_manager.get('user_default_date_format'),
        items_per_page=settings_manager.get('user_default_items_per_page'),
        holiday_region=settings_manager.get('user_default_holiday_region'),
        default_text_color=settings_manager.get('user_default_text_color')
    )
    db.session.add(user)
    return user
