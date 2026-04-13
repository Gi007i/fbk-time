"""Export views.

Provides PDF and iCal export endpoints.
"""

import unicodedata
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlparse, urljoin

from flask import Blueprint, send_file, request, redirect, url_for, abort, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from utils.request_validators import validate_int_param, validate_date_param, validate_year_param
from .pdf import export_absences_pdf, export_user_absences_pdf
from .ical import export_absences_ical
from .matrix import export_team_matrix_pdf
from .services import (
    get_default_date_range,
    build_export_occurrences,
    build_pdf_title,
    build_ical_name
)
from modules.user.services import get_user_or_404

bp = Blueprint('export', __name__, url_prefix='/export')


def _validate_export_range(from_date: date, to_date: date) -> None:
    """Reject unreasonable export ranges (Fail-Fast)."""
    if to_date < from_date:
        abort(400, 'Invalid date range: end before start')


def _safe_filename_segment(value: str) -> str:
    """Return a filesystem-safe segment for Content-Disposition.

    Transliterates Unicode characters (umlauts, accents) to their
    ASCII equivalent before applying werkzeug's secure_filename so
    that names like 'Jörg' survive as 'Jorg' instead of being
    stripped entirely.
    """
    normalized = unicodedata.normalize('NFKD', value)
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii')
    cleaned = secure_filename(ascii_value)
    return cleaned or 'export'


def _resolve_date_range(default_if_empty: bool = True) -> tuple:
    """Resolve date_from/date_to query parameters.

    Rejects the request if exactly one of the two is supplied to avoid
    silent snapping. If both are omitted and ``default_if_empty`` is
    True, falls back to the current month; otherwise returns
    ``(None, None)``.
    """
    from_date = validate_date_param('date_from')
    to_date = validate_date_param('date_to')

    if from_date is None and to_date is None:
        if default_if_empty:
            return get_default_date_range()
        return None, None

    if from_date is None or to_date is None:
        abort(400, 'date_from and date_to must be provided together')

    return from_date, to_date


def _resolve_matrix_range() -> tuple:
    """Resolve matrix export range from query parameters.

    If neither week_start nor week_end is supplied, defaults to the
    current work week (Monday to Friday). If exactly one bound is
    supplied, rejects the request to avoid silent snapping.
    """
    week_start = validate_date_param('week_start')
    week_end = validate_date_param('week_end')

    if week_start is None and week_end is None:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=4)

    if week_start is None or week_end is None:
        abort(400, 'week_start and week_end must be provided together')

    return week_start, week_end


def _redirect_empty_export():
    """Redirect with flash notice when an export yields zero occurrences."""
    flash('Keine Abwesenheiten im gewählten Zeitraum gefunden.', 'warning')
    referer = request.referrer
    if _is_safe_redirect_url(referer):
        return redirect(referer)
    return redirect(url_for('absences.calendar'))


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


def _parse_has_substitute() -> Optional[str]:
    """Validate and return the has_substitute query parameter."""
    value = request.args.get('has_substitute')
    if value and value not in ('yes', 'no'):
        abort(400, 'Invalid has_substitute')
    return value


@bp.route('/pdf')
def export_pdf():
    """Export absences as PDF document with optional filters."""
    if not current_user.is_manager:
        abort(403)
    user_id = validate_int_param('user_id', min_value=1)
    category_id = validate_int_param('category_id', min_value=1)
    has_substitute = _parse_has_substitute()
    include_notes = request.args.get('include_notes', 'false') == 'true'

    from_date, to_date = _resolve_date_range()
    _validate_export_range(from_date, to_date)

    occurrences = build_export_occurrences(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        category_id=category_id,
        has_substitute=has_substitute,
        order_desc=False
    )

    if not occurrences:
        return _redirect_empty_export()

    title = build_pdf_title(user_id, category_id)

    pdf_buffer = export_absences_pdf(
        occurrences,
        title=title,
        include_notes=include_notes,
        date_from=from_date,
        date_to=to_date,
        date_format=current_user.date_format
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
    if not current_user.is_manager:
        abort(403)
    user_id = validate_int_param('user_id', min_value=1)
    category_id = validate_int_param('category_id', min_value=1)
    has_substitute = _parse_has_substitute()

    from_date, to_date = _resolve_date_range()
    _validate_export_range(from_date, to_date)

    occurrences = build_export_occurrences(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        category_id=category_id,
        has_substitute=has_substitute,
        order_desc=False
    )

    if not occurrences:
        return _redirect_empty_export()

    calendar_name = build_ical_name(user_id)
    ical_buffer = export_absences_ical(occurrences, calendar_name)

    filename = f'abwesenheiten_{date.today().strftime("%Y%m%d")}.ics'

    return send_file(
        ical_buffer,
        mimetype='text/calendar',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/user/<int:user_id>/pdf')
def export_user_pdf(user_id):
    """Export absences for a specific user as PDF with optional date filter."""
    if user_id != current_user.id and not current_user.is_manager:
        abort(403)
    user = get_user_or_404(user_id)

    from_date, to_date = _resolve_date_range(default_if_empty=False)

    safe_name = _safe_filename_segment(user.name.lower().replace(' ', '_'))

    if from_date is None and to_date is None:
        year = validate_year_param()
        pdf_buffer = export_user_absences_pdf(
            user, year, date_format=current_user.date_format
        )
        filename = f'abwesenheiten_{safe_name}_{year}.pdf'
    else:
        _validate_export_range(from_date, to_date)
        occurrences = build_export_occurrences(
            from_date=from_date,
            to_date=to_date,
            user_id=user_id,
            order_desc=False
        )

        if not occurrences:
            return _redirect_empty_export()

        pdf_buffer = export_absences_pdf(
            occurrences,
            title=f'Abwesenheiten {user.name}',
            include_notes=True,
            date_from=from_date,
            date_to=to_date,
            date_format=current_user.date_format
        )
        filename = f'abwesenheiten_{safe_name}_{date.today().strftime("%Y%m%d")}.pdf'

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/user/<int:user_id>/ical')
def export_user_ical(user_id):
    """Export absences for a specific user as iCal with optional date filter."""
    if user_id != current_user.id and not current_user.is_manager:
        abort(403)
    user = get_user_or_404(user_id)

    from_date, to_date = _resolve_date_range()
    _validate_export_range(from_date, to_date)

    occurrences = build_export_occurrences(
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        order_desc=False
    )

    if not occurrences:
        return _redirect_empty_export()

    ical_buffer = export_absences_ical(
        occurrences,
        calendar_name=f'Abwesenheiten - {user.name}'
    )

    safe_name = _safe_filename_segment(user.name.lower().replace(' ', '_'))
    filename = f'abwesenheiten_{safe_name}.ics'

    return send_file(
        ical_buffer,
        mimetype='text/calendar',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/user/<int:user_id>/matrix')
def export_user_matrix(user_id):
    """Export matrix for a single user as PDF."""
    if user_id != current_user.id and not current_user.is_manager:
        abort(403)
    user = get_user_or_404(user_id)

    week_start, week_end = _resolve_matrix_range()
    _validate_export_range(week_start, week_end)

    pdf_buffer = export_team_matrix_pdf(week_start, week_end, users=[user])

    safe_name = _safe_filename_segment(user.name.lower().replace(' ', '_'))
    filename = (
        f'matrix_{safe_name}_'
        f'{week_start.strftime("%Y%m%d")}_{week_end.strftime("%Y%m%d")}.pdf'
    )

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


@bp.route('/team-matrix')
def export_team_matrix():
    """Export team matrix (users × days) as PDF for a specific week or month."""
    if not current_user.is_manager:
        abort(403)

    week_start, week_end = _resolve_matrix_range()
    _validate_export_range(week_start, week_end)

    pdf_buffer = export_team_matrix_pdf(week_start, week_end)

    filename = f'team_uebersicht_{week_start.strftime("%Y%m%d")}_{week_end.strftime("%Y%m%d")}.pdf'

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )
