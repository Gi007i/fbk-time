"""Custom decorators.

Provides authentication and access control decorators for RBAC.
"""

from functools import wraps

from flask import abort, redirect, url_for
from flask_login import current_user

from utils.response_helpers import api_error


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
