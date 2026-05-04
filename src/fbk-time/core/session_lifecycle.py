"""Session lifecycle hooks.

Enforces an absolute session lifetime so that silently expired sessions
are converted into an explicit logout with a user-visible message, and
re-initializes session metadata when a session is restored from a
remember-me cookie.
"""

from datetime import datetime, timezone

from flask import flash, redirect, request, session, url_for
from flask_login import current_user, user_loaded_from_cookie


_EXEMPT_ENDPOINTS = ('auth.login', 'auth.logout', 'static')


def register(application) -> None:
    """Register session lifecycle hooks on the application."""

    @user_loaded_from_cookie.connect_via(application, weak=False)
    def _on_remember_cookie_load(sender, user):
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

    @application.before_request
    def enforce_session_expiry():
        if request.endpoint in _EXEMPT_ENDPOINTS:
            return None
        if not current_user.is_authenticated:
            return None
        created_at_raw = session.get('_created_at')
        if not created_at_raw:
            return None
        created_at = datetime.fromisoformat(created_at_raw)
        if datetime.now(timezone.utc) - created_at > application.permanent_session_lifetime:
            from modules.auth.services import logout_user_session
            logout_user_session()
            flash('Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.', 'info')
            return redirect(url_for('auth.login'))
        return None
