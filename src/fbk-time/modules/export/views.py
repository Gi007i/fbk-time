"""Export views.

Provides PDF and iCal export endpoints.
"""

from datetime import date, timedelta
from urllib.parse import urlparse, urljoin

from flask import Blueprint, send_file, request, redirect, url_for
from flask_login import login_required

from utils.request_validators import validate_int_param, validate_date_param, validate_year_param
from .pdf import export_absences_pdf, export_user_absences_pdf
from .ical import export_absences_ical
from .matrix import export_team_matrix_pdf
from .services import (
    get_default_date_range,
    get_export_occurrences,
    build_pdf_title,
    build_ical_name,
    get_user_absences_ordered,
    get_absences_for_export
)
from modules.user.services import get_user_or_404

bp = Blueprint('export', __name__, url_prefix='/export')


def _is_safe_redirect_url(target):
    """Validate redirect URL to prevent open redirect attacks.

    Args:
        target: URL to validate.

    Returns:
        True if URL is safe (same host), False otherwise.
    """
    if not target:
        return False

    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))

    return (
        test_url.scheme in ('http', 'https') and
        ref_url.netloc == test_url.netloc
    )


@bp.before_request
@login_required
def require_login():
    """Require login for all export routes."""
    pass


@bp.route('/pdf')
def export_pdf():
    """Export absences as PDF document with optional filters."""
    user_id = validate_int_param('user_id', min_value=1)
    category_id = validate_int_param('category_id', min_value=1)
    include_notes = request.args.get('include_notes', 'false') == 'true'

    from_date = validate_date_param('date_from')
    to_date = validate_date_param('date_to')

    if not from_date or not to_date:
        default_from, default_to = get_default_date_range()
        from_date = from_date or default_from
        to_date = to_date or default_to

    absences = get_absences_for_export(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        category_id=category_id,
        order_desc=True
    )

    occurrences = get_export_occurrences(absences, from_date, to_date)

    if not occurrences:
        referer = request.referrer
        if _is_safe_redirect_url(referer):
            return redirect(referer)
        return redirect(url_for('absences.calendar'))

    title = build_pdf_title(user_id, category_id)

    pdf_buffer = export_absences_pdf(
        absences,
        title=title,
        include_notes=include_notes,
        date_from=from_date,
        date_to=to_date
    )

    filename = f'abwesenheiten_{date.today().strftime("%Y%m%d")}.pdf'

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/ical')
def export_ical():
    """Export absences as iCal file with optional filters."""
    user_id = validate_int_param('user_id', min_value=1)
    category_id = validate_int_param('category_id', min_value=1)

    from_date = validate_date_param('date_from')
    to_date = validate_date_param('date_to')

    absences = get_absences_for_export(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        category_id=category_id,
        order_desc=False
    )

    if not absences:
        referer = request.referrer
        if _is_safe_redirect_url(referer):
            return redirect(referer)
        return redirect(url_for('absences.calendar'))

    calendar_name = build_ical_name(user_id)
    ical_buffer = export_absences_ical(absences, calendar_name)

    filename = f'abwesenheiten_{date.today().strftime("%Y%m%d")}.ics'

    return send_file(
        ical_buffer,
        mimetype='text/calendar',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/user/<int:user_id>/pdf')
def export_user_pdf(user_id):
    """Export all absences for a specific user as PDF."""
    user = get_user_or_404(user_id)
    year = validate_year_param()

    pdf_buffer = export_user_absences_pdf(user, year)

    filename = f'abwesenheiten_{user.name.lower().replace(" ", "_")}_{year}.pdf'

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/user/<int:user_id>/ical')
def export_user_ical(user_id):
    """Export all absences for a specific user as iCal."""
    user = get_user_or_404(user_id)

    absences = get_user_absences_ordered(user_id)

    if not absences:
        return redirect(url_for('absences.calendar'))

    ical_buffer = export_absences_ical(
        absences,
        calendar_name=f'Abwesenheiten - {user.name}'
    )

    filename = f'abwesenheiten_{user.name.lower().replace(" ", "_")}.ics'

    return send_file(
        ical_buffer,
        mimetype='text/calendar',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/team-matrix')
def export_team_matrix():
    """Export team matrix (users × days) as PDF for a specific week or month."""
    today = date.today()

    week_start = validate_date_param('week_start')
    week_end = validate_date_param('week_end')

    if not week_start:
        week_start = today - timedelta(days=today.weekday())

    if not week_end:
        week_start = week_start - timedelta(days=week_start.weekday())
        week_end = week_start + timedelta(days=4)

    pdf_buffer = export_team_matrix_pdf(week_start, week_end)

    filename = f'team_uebersicht_{week_start.strftime("%Y%m%d")}_{week_end.strftime("%Y%m%d")}.pdf'

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )
