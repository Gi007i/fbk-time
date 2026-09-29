"""Custom decorators.

Provides authentication and access control decorators for RBAC.
"""

from functools import wraps
from urllib.parse import urlsplit, urlunsplit

from flask import abort, redirect, request, url_for

from core.auth import current_user, login_is_fresh
from utils.navigation import is_safe_redirect_url
from utils.response_helpers import api_error, is_ajax_request


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


def reauthentication_response():
    """Send the user to the re-authentication page and back afterwards.

    A GET returns to the requested page itself. Any other method returns to
    the page the action was triggered from, because the action cannot be
    replayed after the detour.
    """
    if request.method == 'GET':
        target = request.full_path.rstrip('?')
    elif is_safe_redirect_url(request.referrer):
        referrer = urlsplit(request.referrer)
        target = urlunsplit(('', '', referrer.path, referrer.query, ''))
    else:
        target = None
    reauth_url = url_for('auth.reauthenticate', next=target)
    if '/api/' in request.path or is_ajax_request():
        return api_error(
            'Bitte bestätigen Sie Ihr Passwort.', status_code=401,
            redirect=reauth_url
        )
    return redirect(reauth_url)


def fresh_login_required(f):
    """Decorator requiring a recent password entry for sensitive actions.

    Sessions without one, including those restored from a remember cookie,
    are sent through the re-authentication page first.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not login_is_fresh():
            return reauthentication_response()
        return f(*args, **kwargs)
    return decorated_function
