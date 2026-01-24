"""Profile views.

Provides user profile page for viewing account information.
"""

from flask import Blueprint, render_template
from flask_login import login_required

from utils.session_navigation import save_return_url

bp = Blueprint('profile', __name__, url_prefix='/profile')


@bp.before_request
@login_required
def require_login():
    """Require login for all profile routes."""
    pass


@bp.route('/')
def index():
    """Display user profile with account information."""
    save_return_url('Profil')
    return render_template('profile/index.html')
