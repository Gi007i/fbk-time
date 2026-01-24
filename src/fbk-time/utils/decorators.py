"""Custom decorators.

Provides authentication and access control decorators for RBAC.
"""

from functools import wraps

from flask import abort, jsonify, redirect, url_for
from flask_login import current_user


def login_required_api(f):
    """Decorator for API endpoints requiring authentication.

    Returns JSON error response instead of redirect for unauthenticated requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({
                'error': 'Unauthorized',
                'message': 'Anmeldung erforderlich.'
            }), 401
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
            return jsonify({
                'error': 'Unauthorized',
                'message': 'Anmeldung erforderlich.'
            }), 401
        if not current_user.is_admin:
            return jsonify({
                'error': 'Forbidden',
                'message': 'Admin-Rechte erforderlich.'
            }), 403
        return f(*args, **kwargs)
    return decorated_function


def manager_required_api(f):
    """API decorator requiring ADMIN or MANAGER role.

    Returns JSON 401/403 response for unauthorized requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({
                'error': 'Unauthorized',
                'message': 'Anmeldung erforderlich.'
            }), 401
        if not current_user.is_manager:
            return jsonify({
                'error': 'Forbidden',
                'message': 'Manager-Rechte erforderlich.'
            }), 403
        return f(*args, **kwargs)
    return decorated_function
