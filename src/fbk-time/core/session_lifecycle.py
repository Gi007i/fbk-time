"""Session lifecycle hooks.

Enforces session expiry so that silently expired sessions are converted
into an explicit logout with a user-visible message, and re-initializes
session metadata when a session is restored from a remember-me cookie.

Two independent limits apply on every request:
    - Absolute lifetime (``PERMANENT_SESSION_LIFETIME``): the maximum age
      of a session measured from login, regardless of activity.
    - Idle timeout (``SESSION_IDLE_TIMEOUT``): the maximum gap between two
      requests. Every non-exempt request refreshes the activity marker,
      so background tabs or a minimized window keep aging the timer and
      eventually expire it. The client cannot extend the session beyond
      its real server interactions. The client-side warning dialog
      surfaces the idle timer and offers an explicit keep-alive request
      (itself a real interaction) to extend it before expiry.

When a session is restored from a remember-me cookie, both markers are
reset to the moment of restoration (see ``initialize_session``). The two
per-request limits therefore only bound the current server session; the
real upper bound on how long a user can stay signed in is the remember
cookie's own duration (``REMEMBER_COOKIE_DURATION``).
"""

from datetime import datetime, timezone

from flask import current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, user_loaded_from_cookie

from utils.response_helpers import is_ajax_request


_EXEMPT_ENDPOINTS = ('auth.login', 'auth.logout', 'static')


def remaining_session_seconds() -> int:
    """Return whole seconds until the current session expires.

    Reflects the smaller of the idle-timeout and absolute-lifetime
    remaining windows, so callers never advertise more time than the
    server will actually honour. Returns 0 when session metadata is
    absent (an already-invalid session). Used by the client session
    warning dialog and the keep-alive endpoint.
    """
    created_at_raw = session.get('_created_at')
    if not created_at_raw:
        return 0

    now = datetime.now(timezone.utc)
    remaining = current_app.permanent_session_lifetime - (
        now - datetime.fromisoformat(created_at_raw)
    )

    idle_timeout = current_app.config['SESSION_IDLE_TIMEOUT']
    if idle_timeout:
        last_activity_raw = session.get('_last_activity')
        if not last_activity_raw:
            return 0
        idle_remaining = idle_timeout - (
            now - datetime.fromisoformat(last_activity_raw)
        )
        if idle_remaining < remaining:
            remaining = idle_remaining

    seconds = int(remaining.total_seconds())
    return seconds if seconds > 0 else 0


def absolute_remaining_seconds() -> int:
    """Return whole seconds until the absolute session lifetime expires.

    Independent of activity: the absolute lifetime cannot be extended, so
    the client uses this as a hard ceiling for its countdown regardless of
    keep-alive activity. Returns 0 when session metadata is absent.
    """
    created_at_raw = session.get('_created_at')
    if not created_at_raw:
        return 0

    now = datetime.now(timezone.utc)
    remaining = current_app.permanent_session_lifetime - (
        now - datetime.fromisoformat(created_at_raw)
    )
    seconds = int(remaining.total_seconds())
    return seconds if seconds > 0 else 0


def register(application) -> None:
    """Register session lifecycle hooks on the application."""

    @user_loaded_from_cookie.connect_via(application, weak=False)
    def _on_remember_cookie_load(sender, user):
        """Re-seed session metadata when a remember-me cookie restores a user.

        Calls ``initialize_session``, which resets both the absolute-lifetime
        and idle-timeout markers to now. As a result those two limits restart
        on every cookie-based restoration, and the effective sign-in ceiling
        is the remember cookie's own duration rather than
        ``PERMANENT_SESSION_LIFETIME``.
        """
        from core.settings_manager import settings_manager
        from modules.auth.models import UserRole
        from modules.auth.services import initialize_session

        # Apply the same access rules as authenticate_user: USER role
        # cannot enter the application while operation_mode is single_user.
        # Returning without initializing leaves _session_version unset,
        # which the check_force_password_change hook treats as an
        # invalidated session and converts into an explicit logout.
        if (user.role == UserRole.USER
                and settings_manager.get('operation_mode') == 'single_user'):
            return

        initialize_session(user)

    def _expire_session():
        from modules.auth.services import logout_user_session
        logout_user_session()
        login_url = url_for('auth.login')
        if is_ajax_request():
            return jsonify({
                'error': 'Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.',
                'redirect': login_url,
            }), 401
        flash('Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.', 'info')
        return redirect(login_url)

    @application.before_request
    def enforce_session_expiry():
        if request.endpoint in _EXEMPT_ENDPOINTS:
            return None
        if not current_user.is_authenticated:
            return None
        created_at_raw = session.get('_created_at')
        if not created_at_raw:
            return _expire_session()

        now = datetime.now(timezone.utc)

        created_at = datetime.fromisoformat(created_at_raw)
        if now - created_at > application.permanent_session_lifetime:
            return _expire_session()

        idle_timeout = application.config['SESSION_IDLE_TIMEOUT']
        if idle_timeout:
            last_activity_raw = session.get('_last_activity')
            if not last_activity_raw:
                return _expire_session()
            last_activity = datetime.fromisoformat(last_activity_raw)
            if now - last_activity > idle_timeout:
                return _expire_session()
            session['_last_activity'] = now.isoformat()

        return None
