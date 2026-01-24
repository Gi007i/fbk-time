"""Export views.

Provides PDF and iCal export endpoints.
"""

from datetime import datetime, date, timedelta
from calendar import monthrange
from urllib.parse import urlparse, urljoin

from flask import Blueprint, send_file, request, redirect, url_for, abort
from flask_login import login_required, current_user

from core.extensions import db

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
from .pdf import (
    export_absences_pdf,
    export_user_absences_pdf,
    export_category_absences_pdf
)
from .ical import export_absences_ical
from .matrix import export_team_matrix_pdf
from modules.absence.models import Absence
from modules.absence.recurrence import recurrence_service
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category


@bp.before_request
@login_required
def require_login():
    """Require login for all export routes."""
    pass


@bp.route('/pdf')
def export_pdf():
    """Export absences as PDF document with optional filters."""
    user_id_str = request.args.get('user_id')
    if user_id_str:
        try:
            user_id = int(user_id_str)
        except ValueError:
            abort(400, 'Invalid user_id')
    else:
        user_id = None

    category_id_str = request.args.get('category_id')
    if category_id_str:
        try:
            category_id = int(category_id_str)
        except ValueError:
            abort(400, 'Invalid category_id')
    else:
        category_id = None

    date_from_str = request.args.get('date_from')
    date_to_str = request.args.get('date_to')
    include_notes = request.args.get('include_notes', 'false') == 'true'

    from_date = None
    to_date = None
    current_year = date.today().year

    if date_from_str:
        if len(date_from_str) != 10:
            abort(400, 'Invalid date format')
        try:
            from_date = datetime.strptime(date_from_str, '%Y-%m-%d').date()
            if from_date.year < current_year - 50 or from_date.year > current_year + 50:
                abort(400, 'Invalid date range')
        except ValueError:
            abort(400, 'Invalid date format')

    if date_to_str:
        if len(date_to_str) != 10:
            abort(400, 'Invalid date format')
        try:
            to_date = datetime.strptime(date_to_str, '%Y-%m-%d').date()
            if to_date.year < current_year - 50 or to_date.year > current_year + 50:
                abort(400, 'Invalid date range')
        except ValueError:
            abort(400, 'Invalid date format')

    if not from_date:
        from_date = date(date.today().year, date.today().month, 1)
    if not to_date:
        _, days = monthrange(from_date.year, from_date.month)
        to_date = date(from_date.year, from_date.month, days)

    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        Absence.start_date <= to_date
    ).filter(
        db.or_(
            Absence.end_date >= from_date,
            Absence.is_recurring == True
        )
    )

    if user_id:
        query = query.filter(Absence.user_id == user_id)

    if category_id:
        query = query.filter(Absence.category_id == category_id)

    absences = query.order_by(Absence.start_date.desc()).all()

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, from_date, to_date
    )

    if not occurrences:
        referer = request.referrer
        if _is_safe_redirect_url(referer):
            return redirect(referer)
        return redirect(url_for('absences.calendar'))

    title_parts = ['Abwesenheitsübersicht']
    if user_id:
        user = db.session.get(User,user_id)
        if user:
            title_parts.append(f'- {user.name}')
    if category_id:
        category = db.session.get(Category,category_id)
        if category:
            title_parts.append(f'({category.name})')

    pdf_buffer = export_absences_pdf(
        absences,
        title=' '.join(title_parts),
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
    user_id_str = request.args.get('user_id')
    if user_id_str:
        try:
            user_id = int(user_id_str)
        except ValueError:
            abort(400, 'Invalid user_id')
    else:
        user_id = None

    category_id_str = request.args.get('category_id')
    if category_id_str:
        try:
            category_id = int(category_id_str)
        except ValueError:
            abort(400, 'Invalid category_id')
    else:
        category_id = None

    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    from_date = None
    to_date = None
    current_year = date.today().year

    if date_from:
        if len(date_from) != 10:
            abort(400, 'Invalid date format')
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            if from_date.year < current_year - 50 or from_date.year > current_year + 50:
                abort(400, 'Invalid date range')
        except ValueError:
            abort(400, 'Invalid date format')

    if date_to:
        if len(date_to) != 10:
            abort(400, 'Invalid date format')
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
            if to_date.year < current_year - 50 or to_date.year > current_year + 50:
                abort(400, 'Invalid date range')
        except ValueError:
            abort(400, 'Invalid date format')

    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True
    )

    if user_id:
        query = query.filter(Absence.user_id == user_id)

    if category_id:
        query = query.filter(Absence.category_id == category_id)

    if from_date and to_date:
        query = query.filter(
            Absence.start_date <= to_date
        ).filter(
            db.or_(
                Absence.end_date >= from_date,
                db.and_(
                    Absence.is_recurring == True,
                    db.or_(
                        Absence.recurrence_end_date >= from_date,
                        Absence.recurrence_end_date.is_(None)
                    )
                )
            )
        )
    elif from_date:
        query = query.filter(
            db.or_(
                Absence.end_date >= from_date,
                db.and_(
                    Absence.is_recurring == True,
                    db.or_(
                        Absence.recurrence_end_date >= from_date,
                        Absence.recurrence_end_date.is_(None)
                    )
                )
            )
        )
    elif to_date:
        query = query.filter(Absence.start_date <= to_date)

    absences = query.order_by(Absence.start_date).all()

    if not absences:
        referer = request.referrer
        if _is_safe_redirect_url(referer):
            return redirect(referer)
        return redirect(url_for('absences.calendar'))

    calendar_name = 'FBK-Time Abwesenheiten'
    if user_id:
        user = db.session.get(User,user_id)
        if user:
            calendar_name = f'Abwesenheiten - {user.name}'

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
    # RBAC: Regular users can only export their own data
    if current_user.role == UserRole.USER and user_id != current_user.id:
        abort(403)

    user = User.query.get_or_404(user_id)
    current_year = date.today().year

    year_str = request.args.get('year')
    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            abort(400, 'Invalid year')
    else:
        year = current_year

    if year < current_year - 50 or year > current_year + 50:
        abort(400, 'Invalid year')

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
    # RBAC: Regular users can only export their own data
    if current_user.role == UserRole.USER and user_id != current_user.id:
        abort(403)

    user = User.query.get_or_404(user_id)

    absences = Absence.query.filter(
        Absence.user_id == user_id
    ).order_by(Absence.start_date).all()

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
    current_year = today.year
    week_start_str = request.args.get('week_start')
    week_end_str = request.args.get('week_end')

    if week_start_str:
        if len(week_start_str) != 10:
            abort(400, 'Invalid date format')
        try:
            week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
            if week_start.year < current_year - 50 or week_start.year > current_year + 50:
                abort(400, 'Invalid date range')
        except ValueError:
            abort(400, 'Invalid date format')
    else:
        week_start = today - timedelta(days=today.weekday())

    if week_end_str:
        if len(week_end_str) != 10:
            abort(400, 'Invalid date format')
        try:
            week_end = datetime.strptime(week_end_str, '%Y-%m-%d').date()
            if week_end.year < current_year - 50 or week_end.year > current_year + 50:
                abort(400, 'Invalid date range')
        except ValueError:
            abort(400, 'Invalid date format')
    else:
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
