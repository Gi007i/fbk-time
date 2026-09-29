"""License display routes.

Provides a page showing all open source dependencies and their licenses.
"""

from flask import Blueprint, render_template

from core.auth import login_required
from .services import get_all_licenses, get_license_stats

bp = Blueprint('licenses', __name__, url_prefix='/licenses')


@bp.route('/')
@login_required
def index():
    """Display all open source licenses used by the application."""
    licenses = get_all_licenses()
    summary = get_license_stats()

    return render_template(
        'licenses/index.html',
        licenses=licenses,
        summary=summary
    )
