"""License display routes.

Provides a public page showing all open source dependencies and their licenses.
"""

from flask import Blueprint, render_template

from core.licenses import load_licenses, get_license_summary

bp = Blueprint('licenses', __name__, url_prefix='/licenses')


@bp.route('/')
def index():
    """Display all open source licenses used by the application."""
    licenses = load_licenses()
    summary = get_license_summary()

    return render_template(
        'licenses/index.html',
        licenses=licenses,
        summary=summary
    )
