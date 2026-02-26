"""Custom decorators.

Provides authentication and access control decorators for RBAC.
"""

from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_fresh

from utils.response_helpers import api_error
from utils.session_navigation import is_ajax_request


def login_required_api(f):
    """Decorator for API endpoints requiring authentication.

    Returns JSON error response instead of redirect for unauthenticated requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return api_error('Anmeldung erforderlich.', status_code=401)
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator requiring ADMIN role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def manager_required(f):
    """Decorator requiring ADMIN or MANAGER role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not current_user.is_manager:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def admin_required_api(f):
    """API decorator requiring ADMIN role.

    Returns JSON 401/403 response for unauthorized requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return api_error('Anmeldung erforderlich.', status_code=401)
        if not current_user.is_admin:
            return api_error('Admin-Rechte erforderlich.', status_code=403)
        return f(*args, **kwargs)
    return decorated_function


def fresh_session_required(f):
    """Decorator requiring a fresh login session for sensitive operations.

    Returns JSON error for AJAX requests, redirect for regular requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not login_fresh():
            if is_ajax_request():
                return api_error('Sitzung abgelaufen. Bitte erneut anmelden.', status_code=401)
            flash('Bitte melden Sie sich erneut an für diese Aktion.', 'warning')
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def manager_required_api(f):
    """API decorator requiring ADMIN or MANAGER role.

    Returns JSON 401/403 response for unauthorized requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return api_error('Anmeldung erforderlich.', status_code=401)
        if not current_user.is_manager:
            return api_error('Manager-Rechte erforderlich.', status_code=403)
        return f(*args, **kwargs)
    return decorated_function
