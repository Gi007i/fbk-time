"""Authentication services.

Provides session management, Argon2id password verification,
and account lockout protection with RBAC status checks.
"""

from datetime import datetime, timezone, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from flask import session
from sqlalchemy import and_, delete, or_, select

from config import Config
from core import auth
from core.auth import (
    SESSION_FRESH_KEY,
    client_network,
    current_user,
    login_user,
    logout_user,
    mark_login_fresh,
)
from core.db import db
from core.settings_manager import settings_manager
from utils.validators import normalize_email, normalize_username
from .models import User, UserStatus, UserRole, LoginAttempt, LoginAttemptType


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


@auth.user_loader
def load_user(user_id):
    """Load user by their stored session identity.

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


@auth.remember_restorer
def restore_from_remember_cookie(user):
    """Gate and re-seed a session restored from a remember-me cookie.

    Applies the single_user access rule of authenticate_user; a refused
    restore establishes no session and revokes the remember cookie.

    Returns:
        True when the restored session may be established.
    """
    if (user.role == UserRole.USER
            and settings_manager.get('operation_mode') == 'single_user'):
        return False

    initialize_session(user)
    return True


def authenticate_user(username: str, password: str) -> User | None:
    """Authenticate user with constant-time behavior and status check.

    Prevents user enumeration via timing analysis by always performing
    a hash verification, even when the user doesn't exist.

    Args:
        username: Username to authenticate.
        password: Password to verify.

    Returns:
        User instance if the password matches and the account may log in,
        otherwise None. Callers show one generic message for every failure.
    """
    user = db.session.scalars(
        select(User).filter_by(username=username)
    ).first()

    hash_to_verify = user.password_hash if user else DUMMY_HASH

    try:
        ph.verify(hash_to_verify, password)
        password_valid = True

        if user and ph.check_needs_rehash(user.password_hash):
            user.password_hash = ph.hash(password)
            db.session.commit()

    except (VerifyMismatchError, InvalidHashError):
        password_valid = False

    if not user or not password_valid:
        return None

    if user.status != UserStatus.ACTIVE:
        return None

    if (settings_manager.get('operation_mode') == 'single_user'
            and user.role == UserRole.USER):
        return None

    return user


def verify_current_password(user: User, password: str) -> bool:
    """Check the current password of a signed-in user.

    A mismatch computes one extra hash so its response time matches the
    success path, which goes on to hash the new password.

    Args:
        user: Signed-in user.
        password: Password entered as the current one.

    Returns:
        True if the password matches.
    """
    try:
        ph.verify(user.password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        ph.hash('timing_attack_prevention_dummy')
        return False


def initialize_session(user):
    """Populate session metadata for an authenticated user.

    Resets the absolute-lifetime and idle-timeout markers, so after a
    remember-me restore the sign-in ends with the remember cookie's
    original expiry, not ``PERMANENT_SESSION_LIFETIME``.

    Args:
        user: Authenticated user instance.
    """
    # Non-persistent cookie (OWASP Session Management): closing the browser
    # ends the session; both limits stay enforced server-side by the markers.
    session.permanent = False
    now = datetime.now(timezone.utc).isoformat()
    session['_created_at'] = now
    session['_last_activity'] = now
    if user.role == UserRole.USER:
        session['_session_version'] = settings_manager.get('user_session_version')


def regenerate_session(user, remember=False):
    """Replace the current session with a fresh one for a user.

    Clearing before login_user prevents session fixation; after a
    credential_version rotation it also re-signs this session with the new
    identity so only the other devices are logged out. The same user keeps
    the time of the last password entry, so rotating the identity does not
    demand a new re-authentication.

    Args:
        user: User instance to sign in.
        remember: If True, issue a remember-me cookie.
    """
    fresh_at = None
    if current_user.is_authenticated and current_user.id == user.id:
        fresh_at = session.get(SESSION_FRESH_KEY)
    session.clear()
    login_user(user, remember=remember)
    initialize_session(user)
    if fresh_at is not None:
        session[SESSION_FRESH_KEY] = fresh_at


def login_user_session(user, remember=False):
    """Record the login timestamps and start a fresh session.

    The password was just entered, so the session counts as fresh for
    sensitive actions.

    Args:
        user: User instance to log in.
        remember: If True, issue a remember-me cookie.
    """
    user.previous_login_at = user.last_login_at
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()

    regenerate_session(user, remember=remember)
    mark_login_fresh()


def logout_user_session():
    """Log out the current user and drop all session state."""
    logout_user()
    session.clear()


def _find_attempt(identifier, identifier_type):
    """Return the attempt counter for an identifier within its namespace."""
    return db.session.scalars(
        select(LoginAttempt).filter_by(
            identifier=identifier, identifier_type=identifier_type
        )
    ).first()


# Exact IPv4 address: one address per client is the common case, and a
# wider network would let one client lock out its neighbours.
_IP_COUNTER_IPV4_PREFIX = 32


def _ip_counter_id(ip_address):
    """Return the identifier an address is counted under (IPv6: its /64)."""
    return client_network(ip_address, _IP_COUNTER_IPV4_PREFIX)


def _utc_now_naive():
    """Return the current UTC time in the naive form stored by SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lock_remaining(attempt):
    """Return the seconds left on a counter's hard lock, 0 if unlocked.

    An expired lock deletes the counter, so counting starts over.
    """
    if not attempt or not attempt.locked_until:
        return 0

    now_utc = _utc_now_naive()
    if now_utc < attempt.locked_until:
        return int((attempt.locked_until - now_utc).total_seconds()) + 1

    db.session.delete(attempt)
    db.session.commit()
    return 0


# Beyond this the delay has long reached any configurable maximum; the cap
# only keeps the power bounded while the counter keeps growing.
_DELAY_EXPONENT_CAP = 30


def _delay_remaining(attempt):
    """Return the seconds left on a counter's progressive delay."""
    if (not attempt or not attempt.last_attempt
            or not _get_delay_enabled() or attempt.attempt_count == 0):
        return 0

    exponent = min(attempt.attempt_count - 1, _DELAY_EXPONENT_CAP)
    delay = min(_get_delay_base_seconds() * (2 ** exponent), _get_delay_max_seconds())
    next_allowed = attempt.last_attempt + timedelta(seconds=delay)
    now_utc = _utc_now_naive()
    if now_utc < next_allowed:
        return int((next_allowed - now_utc).total_seconds()) + 1
    return 0


def _increment_attempt(identifier, identifier_type, now_utc):
    """Count one failed attempt on a counter, creating it when absent."""
    attempt = _find_attempt(identifier, identifier_type)
    if not attempt:
        attempt = LoginAttempt(
            identifier=identifier,
            identifier_type=identifier_type,
            attempt_count=0
        )
        db.session.add(attempt)

    attempt.attempt_count += 1
    attempt.last_attempt = now_utc
    return attempt


def _trusted_device(device_id):
    """Report whether a device entry exempts from the account lockout.

    An entry whose own counter reached the threshold loses the exemption
    until its lock expires (OWASP device cookie lockout), so a stolen
    cookie cannot be used for unthrottled guessing.
    """
    if device_id is None:
        return False
    return _lock_remaining(_find_attempt(device_id, LoginAttemptType.DEVICE)) == 0


def login_wait_seconds(username, ip_address, device_id):
    """Return how long a password entry for a username must wait.

    Clients without a trusted device entry for the username are subject to
    the account's hard lock and progressive delay and to the IP lock. A
    trusted device skips all of them, so failures from elsewhere, even
    from its own network, can neither slow down nor lock out its owner.

    Args:
        username: Username to check.
        ip_address: Client IP address.
        device_id: Id of the browser's device entry for the username, or
            None (see core.auth.known_device_id).

    Returns:
        Remaining seconds to wait, or 0 if the attempt is allowed.
    """
    if _trusted_device(device_id):
        return 0

    account = _find_attempt(username, LoginAttemptType.USERNAME)
    if account and account.locked_until:
        account_wait = _lock_remaining(account)
    else:
        account_wait = _delay_remaining(account)
    ip_wait = _lock_remaining(
        _find_attempt(_ip_counter_id(ip_address), LoginAttemptType.IP_ADDRESS)
    )
    return max(account_wait, ip_wait)


# Well above the per-account threshold, so users behind one shared address
# are not locked out together while credential stuffing still is.
_IP_LOCKOUT_MULTIPLIER = 5


def record_failed_login(username, ip_address, device_id):
    """Record a failed password entry and lock counters at their threshold.

    A failure from a trusted device counts on that entry's counter, so the
    owner's typos neither slow down nor lock the account for other clients;
    at the threshold the entry loses its trust for the lockout duration.
    Every other failure counts on the account, which locks for the lockout
    duration at the threshold. Every failure also counts on the IP counter
    (defense in depth; primary rate limiting is done by Nginx).

    Args:
        username: Username the attempt was made for.
        ip_address: Client IP address.
        device_id: Id of the browser's device entry for the username, or
            None.
    """
    now_utc = _utc_now_naive()
    threshold = _get_lockout_threshold()
    if _trusted_device(device_id):
        attempt = _increment_attempt(device_id, LoginAttemptType.DEVICE, now_utc)
    else:
        attempt = _increment_attempt(username, LoginAttemptType.USERNAME, now_utc)
    if attempt.attempt_count >= threshold:
        attempt.locked_until = now_utc + _get_lockout_duration()

    ip_attempt = _increment_attempt(
        _ip_counter_id(ip_address), LoginAttemptType.IP_ADDRESS, now_utc
    )
    if ip_attempt.attempt_count >= threshold * _IP_LOCKOUT_MULTIPLIER:
        ip_attempt.locked_until = now_utc + _get_lockout_duration()

    db.session.commit()


def clear_failed_attempts(username, device_id):
    """Clear the account and device counters after a successful password check.

    The IP counter is left to expire on its own: otherwise a sprayer with
    a valid account of their own could reset it by signing in between
    guesses. Known browsers skip the IP lock, so a shared network does not
    lock out its regular users.

    Args:
        username: Username that logged in.
        device_id: Id of the browser's device entry for the username, or
            None.
    """
    db.session.execute(
        delete(LoginAttempt).where(or_(
            and_(LoginAttempt.identifier == username,
                 LoginAttempt.identifier_type == LoginAttemptType.USERNAME),
            and_(LoginAttempt.identifier == device_id,
                 LoginAttempt.identifier_type == LoginAttemptType.DEVICE),
        ))
    )
    db.session.commit()


def clear_login_attempts_for_username(username):
    """Delete the account counter of a username, lifting its lockout.

    Used when an admin unlocks an account, so the person can sign in again
    right away from every client. The caller commits.

    Args:
        username: Username whose counter is removed.
    """
    db.session.execute(
        delete(LoginAttempt).where(
            LoginAttempt.identifier == username,
            LoginAttempt.identifier_type == LoginAttemptType.USERNAME
        )
    )


def cleanup_expired_lockouts():
    """Remove expired lockout records and stale attempts from the database.

    Deletes:
    1. Entries with expired lockout (locked_until < now)
    2. Entries without lockout older than attempt_retention_hours
    3. Entries of a counter type this version does not know

    Bulk deletes never load rows, so a type value the enum does not know
    (left by a pre-release build) cannot raise here.

    Returns:
        Number of records removed.
    """
    now_utc = _utc_now_naive()
    retention_hours = settings_manager.get('lockout_attempt_retention_hours')
    retention_cutoff = now_utc - timedelta(hours=retention_hours)

    count = db.session.execute(
        delete(LoginAttempt).where(or_(
            and_(LoginAttempt.locked_until.isnot(None),
                 LoginAttempt.locked_until < now_utc),
            and_(LoginAttempt.locked_until.is_(None),
                 LoginAttempt.last_attempt < retention_cutoff),
            LoginAttempt.identifier_type.not_in(list(LoginAttemptType)),
        ))
    ).rowcount

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

    inactive_users = db.session.scalars(
        select(User).where(
            User.status == UserStatus.ACTIVE,
            User.role != UserRole.ADMIN,
            or_(
                and_(
                    User.last_login_at.isnot(None),
                    User.last_login_at < inactive_cutoff
                ),
                and_(
                    User.last_login_at.is_(None),
                    User.created_at < inactive_cutoff
                )
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


def get_account_lockouts(usernames: list[str]) -> dict[str, dict]:
    """Get the failed-attempt count and lock state of each counted username.

    Args:
        usernames: List of usernames to check.

    Returns:
        Dict mapping username to ``{'count': int, 'locked': bool}``.
    """
    if not usernames:
        return {}

    attempts = db.session.scalars(
        select(LoginAttempt).where(
            LoginAttempt.identifier.in_(usernames),
            LoginAttempt.identifier_type == LoginAttemptType.USERNAME
        )
    ).all()

    now_utc = _utc_now_naive()
    return {
        attempt.identifier: {
            'count': attempt.attempt_count,
            'locked': (attempt.locked_until is not None
                       and attempt.locked_until > now_utc),
        }
        for attempt in attempts
    }


def self_registration_available() -> bool:
    """Report whether self-registration may be offered and accepted.

    Single-user mode refuses login to the USER role, so registering there
    would only create accounts that can never sign in.

    Returns:
        True when registration is enabled and the operation mode allows it.
    """
    return (settings_manager.get('self_registration_enabled')
            and settings_manager.get('operation_mode') != 'single_user')


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
        username=normalize_username(username),
        name=name.strip(),
        email=normalize_email(email),
        password_hash=hash_password(password),
        status=UserStatus.PENDING,
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
