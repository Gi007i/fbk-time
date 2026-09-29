"""Session lifecycle hooks.

Turns an expired session into an explicit logout, remember-me cookie
included, with a user-visible message. Two limits apply per request: the
absolute lifetime since login and the idle timeout between two requests.
"""

from datetime import datetime, timezone

from flask import current_app, flash, jsonify, redirect, request, session, url_for

from core.auth import current_user, logout_user
from utils.response_helpers import is_ajax_request


_EXEMPT_ENDPOINTS = ('auth.login', 'auth.logout', 'static')


def remaining_session_seconds() -> int:
    """Return whole seconds until the current session expires.

    The smaller of the idle and absolute windows, so callers never
    advertise more time than the server honours; 0 without session metadata.
    """
    created_at_raw = session.get('_created_at')
    if not created_at_raw:
        return 0

    now = datetime.now(timezone.utc)
    remaining = current_app.permanent_session_lifetime - (
        now - datetime.fromisoformat(created_at_raw)
    )

    last_activity_raw = session.get('_last_activity')
    if not last_activity_raw:
        return 0
    idle_remaining = current_app.config['SESSION_IDLE_TIMEOUT'] - (
        now - datetime.fromisoformat(last_activity_raw)
    )
    if idle_remaining < remaining:
        remaining = idle_remaining

    seconds = int(remaining.total_seconds())
    return seconds if seconds > 0 else 0


def absolute_remaining_seconds() -> int:
    """Return whole seconds until the absolute session lifetime expires.

    Activity cannot extend it, so the client uses it as a hard countdown
    ceiling; 0 without session metadata.
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

    def _expire_session():
        logout_user()
        session.clear()
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

        last_activity_raw = session.get('_last_activity')
        if not last_activity_raw:
            return _expire_session()
        last_activity = datetime.fromisoformat(last_activity_raw)
        if now - last_activity > application.config['SESSION_IDLE_TIMEOUT']:
            return _expire_session()
        session['_last_activity'] = now.isoformat()

        return None
