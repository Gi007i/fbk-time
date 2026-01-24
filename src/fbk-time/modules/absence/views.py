"""Absence views.

Provides CRUD routes and occurrence management for absences.
"""

from datetime import date, datetime, timedelta
from calendar import monthrange
from flask import Blueprint, render_template, redirect, url_for, request, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_

from core.extensions import db
from utils.session_navigation import is_ajax_request, save_return_url, get_return_url, get_return_info
from utils.response_helpers import ajax_response
from utils.helpers import format_date_for_user

bp = Blueprint('absences', __name__, url_prefix='/absences')
from .models import Absence, AbsenceHistory
from .forms import AbsenceForm, OccurrenceEditForm
from .validation import (
    check_absence_conflicts,
    validate_substitute_required,
    validate_substitute_not_self,
    validate_date_range,
)
from .history import create_initial_history, track_absence_changes
from .recurrence import recurrence_service
from modules.auth.models import User, UserRole, UserStatus
from modules.category.models import Category
from modules.holidays.services import get_holidays_for_month, count_working_days


def can_modify_absence(absence):
    """Check if current user can modify an absence.

    Returns True if user owns the absence or is Manager/Admin.
    """
    if absence.user_id == current_user.id:
        return True
    if current_user.role in (UserRole.ADMIN, UserRole.MANAGER):
        return True
    return False


@bp.before_request
@login_required
def require_login():
    """Require login for all absence routes."""
    pass


@bp.route('/')
def index():
    """Redirect to calendar view (default view)."""
    return redirect(url_for('absences.calendar'))


@bp.route('/list')
def list_absences():
    """Display expanded absence occurrences with filtering."""
    save_return_url('Liste')
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

    has_substitute = request.args.get('has_substitute') or None

    today = date.today()
    current_year = today.year
    date_from_str = request.args.get('date_from')
    date_to_str = request.args.get('date_to')

    if date_from_str:
        if len(date_from_str) != 10:
            abort(400, 'Invalid date format')
        try:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        except ValueError:
            abort(400, 'Invalid date format')
        if date_from.year < current_year - 50 or date_from.year > current_year + 50:
            abort(400, 'Invalid date range')
    else:
        date_from = today.replace(day=1)

    if date_to_str:
        if len(date_to_str) != 10:
            abort(400, 'Invalid date format')
        try:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
        except ValueError:
            abort(400, 'Invalid date format')
        if date_to.year < current_year - 50 or date_to.year > current_year + 50:
            abort(400, 'Invalid date range')
    else:
        _, last_day = monthrange(date_from.year, date_from.month)
        date_to = date_from.replace(day=last_day)

    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) &
            (Absence.start_date <= date_to) &
            (Absence.end_date >= date_from),
            (Absence.is_recurring == True) &
            (Absence.start_date <= date_to) &
            ((Absence.recurrence_end_date >= date_from) | (Absence.recurrence_end_date == None))
        )
    )

    if user_id:
        query = query.filter(Absence.user_id == user_id)

    if category_id:
        query = query.filter(Absence.category_id == category_id)

    absences = query.all()

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, date_from, date_to
    )

    if has_substitute == 'yes':
        occurrences = [o for o in occurrences if o['absence'].substitute_id]
    elif has_substitute == 'no':
        occurrences = [o for o in occurrences if not o['absence'].substitute_id]

    occurrences.sort(key=lambda o: o['date'])

    per_page = current_user.items_per_page
    total = len(occurrences)

    # per_page == 0 means show all (no pagination)
    if per_page == 0:
        page = 1
        total_pages = 1
        paginated_occurrences = occurrences
        has_prev = False
        has_next = False
    else:
        page_str = request.args.get('page')
        if page_str:
            try:
                page = int(page_str)
            except ValueError:
                abort(400, 'Invalid page number')
        else:
            page = 1

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        if page < 1:
            abort(400, 'Invalid page number')

        # Redirect to last valid page if current page no longer exists
        if page > total_pages:
            args = request.args.to_dict()
            args['page'] = str(total_pages)
            return redirect(url_for('absences.list_absences', **args))

        start = (page - 1) * per_page
        end = start + per_page

        paginated_occurrences = occurrences[start:end]
        has_prev = page > 1
        has_next = page < total_pages

    users = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all()
    categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()

    return render_template(
        'absences/list.html',
        occurrences=paginated_occurrences,
        users=users,
        categories=categories,
        filters={
            'user_id': user_id,
            'category_id': category_id,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'has_substitute': has_substitute
        },
        pagination={
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages,
            'has_prev': has_prev,
            'has_next': has_next
        }
    )


@bp.route('/calendar')
def calendar():
    """Display calendar view of absences."""
    save_return_url('Kalender')
    today = date.today()
    current_year = today.year

    year_str = request.args.get('year')
    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            abort(400, 'Invalid year')
    else:
        year = today.year

    if year < current_year - 50 or year > current_year + 50:
        abort(400, 'Invalid year')

    month_str = request.args.get('month')
    if month_str:
        try:
            month = int(month_str)
        except ValueError:
            abort(400, 'Invalid month')
    else:
        month = today.month

    if month < 1 or month > 12:
        abort(400, 'Invalid month')

    week_start_str = request.args.get('week_start')
    if week_start_str:
        if len(week_start_str) != 10:
            abort(400, 'Invalid date format')
        try:
            week_start = date.fromisoformat(week_start_str)
        except ValueError:
            abort(400, 'Invalid date format')
        if week_start.year < current_year - 50 or week_start.year > current_year + 50:
            abort(400, 'Invalid date range')
        week_start = week_start - timedelta(days=week_start.weekday())
    else:
        week_start = today - timedelta(days=today.weekday())

    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    first_day = date(year, month, 1)
    _, last_day_num = monthrange(year, month)
    last_day = date(year, month, last_day_num)

    week_end = week_start + timedelta(days=6)
    range_start = min(week_start, first_day)
    range_end = max(week_end, last_day)

    user_status_filter = User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])

    query = Absence.query.join(
        User, Absence.user_id == User.id
    ).join(Category).filter(
        user_status_filter,
        User.role == UserRole.USER,
        Category.active == True,
        or_(
            (Absence.is_recurring == False) & (Absence.start_date <= range_end) & (Absence.end_date >= range_start),
            (Absence.is_recurring == True) & (Absence.start_date <= range_end) & (
                (Absence.recurrence_end_date >= range_start) | (Absence.recurrence_end_date == None)
            )
        )
    )

    user_id = None
    user_id_str = request.args.get('user_id')
    if user_id_str:
        try:
            user_id = int(user_id_str)
        except ValueError:
            abort(400, 'Invalid user_id')
        query = query.filter(Absence.user_id == user_id)

    category_id = None
    category_id_str = request.args.get('category_id')
    if category_id_str:
        try:
            category_id = int(category_id_str)
        except ValueError:
            abort(400, 'Invalid category_id')
        query = query.filter(Absence.category_id == category_id)

    absences = query.order_by(Absence.start_date).all()

    expanded_occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, range_start, range_end
    )

    holidays = get_holidays_for_month(year, month)
    if week_start.month != month or week_start.year != year:
        holidays.update(get_holidays_for_month(week_start.year, week_start.month))
    if week_end.month != week_start.month:
        holidays.update(get_holidays_for_month(week_end.year, week_end.month))

    users = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all()
    categories = Category.query.order_by(Category.sort_order).all()

    return render_template(
        'absences/calendar.html',
        year=year,
        month=month,
        absences=absences,
        occurrences=expanded_occurrences,
        holidays=holidays,
        users=users,
        categories=categories,
        today=today,
        prev_week=prev_week,
        next_week=next_week
    )


@bp.route('/create', methods=['GET', 'POST'])
def create():
    """Create a new absence record."""
    form = AbsenceForm()

    users = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all()
    categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()

    # Regular users can only create absences for themselves
    is_manager = current_user.role in (UserRole.ADMIN, UserRole.MANAGER)
    if is_manager:
        form.user_id.choices = [('', '-- Person auswählen --')] + [(u.id, u.name) for u in users]
    else:
        form.user_id.choices = [(current_user.id, current_user.name)]
        form.user_id.data = current_user.id

    form.category_id.choices = [('', '-- Kategorie auswählen --')] + [(c.id, c.name) for c in categories]
    form.substitute_id.choices = [('', '-- Keine Vertretung --')] + [
        (str(u.id), u.name) for u in users
    ]

    if form.validate_on_submit():
        # IDOR protection: Regular users can only create for themselves
        if not is_manager and form.user_id.data != current_user.id:
            abort(403)

        is_valid, error = validate_date_range(form.start_date.data, form.end_date.data)
        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            return render_template('absences/create.html', form=form, is_manager=is_manager)

        is_valid, error = validate_substitute_required(
            form.category_id.data,
            form.substitute_id.data
        )
        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            return render_template('absences/create.html', form=form, is_manager=is_manager)

        is_valid, error = validate_substitute_not_self(
            form.user_id.data,
            form.substitute_id.data
        )
        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            return render_template('absences/create.html', form=form, is_manager=is_manager)

        conflicts = check_absence_conflicts(
            form.user_id.data,
            form.start_date.data,
            form.end_date.data,
            substitute_id=form.substitute_id.data
        )

        time_flags = form.get_time_flags()

        recurrence_data = form.get_recurrence_data()

        absence = Absence(
            user_id=form.user_id.data,
            category_id=form.category_id.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data if not recurrence_data['is_recurring'] else form.start_date.data,
            start_time=time_flags['start_time'],
            end_time=time_flags['end_time'],
            is_all_day=time_flags['is_all_day'],
            is_half_day_morning=time_flags['is_half_day_morning'],
            is_half_day_afternoon=time_flags['is_half_day_afternoon'],
            substitute_id=form.substitute_id.data,
            notes=form.notes.data.strip() if form.notes.data else None,
            is_recurring=recurrence_data['is_recurring'],
            rrule=recurrence_data['rrule'],
            recurrence_end_date=recurrence_data['recurrence_end_date']
        )

        db.session.add(absence)
        db.session.flush()

        create_initial_history(absence)

        db.session.commit()

        user = db.session.get(User, form.user_id.data)

        if absence.is_recurring:
            occurrence_count = recurrence_service.count_occurrences(absence)
            pattern_desc = recurrence_service.get_recurrence_description(
                absence.rrule, absence.recurrence_end_date
            )
            message = (
                f'Wiederkehrende Abwesenheit für "{user.name}" erstellt: '
                f'{pattern_desc} ({occurrence_count} Termine).'
            )
        else:
            message = (
                f'Abwesenheit für "{user.name}" vom '
                f'{format_date_for_user(absence.start_date)} bis '
                f'{format_date_for_user(absence.end_date)} wurde erstellt.'
            )

        return_to = get_return_url('absences.calendar')
        if is_ajax_request():
            response_data = {'warnings': conflicts.messages} if conflicts.has_conflicts else {}
            return ajax_response(success=True, message=message, redirect=return_to, **response_data)
        return redirect(return_to)

    return render_template('absences/create.html', form=form, is_manager=is_manager)


@bp.route('/<int:id>')
def detail(id):
    """Display absence details with history."""
    absence = Absence.query.get_or_404(id)
    origin = get_return_info('absences.list_absences', 'Liste')

    history = AbsenceHistory.query.filter_by(
        absence_id=id
    ).order_by(AbsenceHistory.changed_at.desc()).all()

    working_days = count_working_days(absence.start_date, absence.end_date)

    recurrence_info = None
    occurrences = []
    if absence.is_recurring:
        occurrence_count = recurrence_service.count_occurrences(absence)
        exception_count = absence.exceptions.count()
        recurrence_info = {
            'description': recurrence_service.get_recurrence_description(
                absence.rrule, absence.recurrence_end_date
            ),
            'occurrence_count': occurrence_count,
            'exception_count': exception_count,
            'deleted_count': absence.exceptions.filter_by(exception_type='deleted').count(),
            'modified_count': absence.exceptions.filter_by(exception_type='modified').count()
        }

        for occ_date, exception in recurrence_service.expand_occurrences(
            absence, absence.start_date, absence.recurrence_end_date
        ):
            occ_data = recurrence_service.get_occurrence_data(absence, occ_date)
            if occ_data:
                occurrences.append({
                    'date': occ_date,
                    'is_exception': occ_data['is_exception'],
                    'category': occ_data['category'],
                    'is_half_day_morning': occ_data['is_half_day_morning'],
                    'is_half_day_afternoon': occ_data['is_half_day_afternoon']
                })

    return render_template(
        'absences/detail.html',
        absence=absence,
        origin=origin,
        history=history,
        working_days=working_days,
        recurrence_info=recurrence_info,
        occurrences=occurrences
    )


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
def edit(id):
    """Edit an existing absence record."""
    absence = Absence.query.get_or_404(id)

    # IDOR protection
    if not can_modify_absence(absence):
        abort(403)

    form = AbsenceForm(obj=absence)

    users = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all()
    categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()

    is_manager = current_user.role in (UserRole.ADMIN, UserRole.MANAGER)

    user_ids = {u.id for u in users}
    if absence.user_id not in user_ids:
        users = list(users) + [absence.user]

    category_ids = {c.id for c in categories}
    if absence.category_id not in category_ids:
        categories = list(categories) + [absence.category]

    # Regular users can only edit their own absences
    if is_manager:
        form.user_id.choices = [('', '-- Person auswählen --')] + [(u.id, u.name) for u in users]
    else:
        form.user_id.choices = [(current_user.id, current_user.name)]

    form.category_id.choices = [('', '-- Kategorie auswählen --')] + [(c.id, c.name) for c in categories]

    substitute_users = [u for u in User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all() if u.id != absence.user_id]

    if absence.substitute_id and absence.substitute_id not in {u.id for u in substitute_users}:
        substitute_users.append(absence.substitute)

    form.substitute_id.choices = [('', '-- Keine Vertretung --')] + [
        (str(u.id), u.name) for u in substitute_users
    ]

    if request.method == 'GET':
        form.set_time_type_from_absence(absence)
        form.set_recurrence_from_absence(absence)

    if form.validate_on_submit():
        is_valid, error = validate_date_range(form.start_date.data, form.end_date.data)
        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)

        is_valid, error = validate_substitute_required(
            form.category_id.data,
            form.substitute_id.data
        )
        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)

        is_valid, error = validate_substitute_not_self(
            form.user_id.data,
            form.substitute_id.data
        )
        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)

        conflicts = check_absence_conflicts(
            form.user_id.data,
            form.start_date.data,
            form.end_date.data,
            exclude_absence_id=id,
            substitute_id=form.substitute_id.data
        )

        time_flags = form.get_time_flags()

        recurrence_data = form.get_recurrence_data()

        form_data = {
            'user_id': form.user_id.data,
            'category_id': form.category_id.data,
            'start_date': form.start_date.data,
            'end_date': form.end_date.data if not recurrence_data['is_recurring'] else form.start_date.data,
            'start_time': time_flags['start_time'],
            'end_time': time_flags['end_time'],
            'is_all_day': time_flags['is_all_day'],
            'is_half_day_morning': time_flags['is_half_day_morning'],
            'is_half_day_afternoon': time_flags['is_half_day_afternoon'],
            'substitute_id': form.substitute_id.data,
            'notes': form.notes.data.strip() if form.notes.data else None
        }

        track_absence_changes(absence, form_data)

        absence.user_id = form.user_id.data
        absence.category_id = form.category_id.data
        absence.start_date = form.start_date.data
        absence.end_date = form.end_date.data if not recurrence_data['is_recurring'] else form.start_date.data
        absence.start_time = time_flags['start_time']
        absence.end_time = time_flags['end_time']
        absence.is_all_day = time_flags['is_all_day']
        absence.is_half_day_morning = time_flags['is_half_day_morning']
        absence.is_half_day_afternoon = time_flags['is_half_day_afternoon']
        absence.substitute_id = form.substitute_id.data
        absence.notes = form.notes.data.strip() if form.notes.data else None
        absence.is_recurring = recurrence_data['is_recurring']
        absence.rrule = recurrence_data['rrule']
        absence.recurrence_end_date = recurrence_data['recurrence_end_date']

        db.session.commit()

        message = 'Abwesenheit wurde aktualisiert.'
        return_to = url_for('absences.detail', id=id)
        if is_ajax_request():
            response_data = {'warnings': conflicts.messages} if conflicts.has_conflicts else {}
            return ajax_response(success=True, message=message, redirect=return_to, **response_data)
        return redirect(return_to)

    return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)


@bp.route('/<int:id>/delete', methods=['POST'])
def delete(id):
    """Delete an absence record (CASCADE to history)."""
    absence = Absence.query.get_or_404(id)

    # IDOR protection
    if not can_modify_absence(absence):
        abort(403)

    user_name = absence.user.name if absence.user else 'Unbekannt'
    date_range = f'{format_date_for_user(absence.start_date)} - {format_date_for_user(absence.end_date)}'

    db.session.delete(absence)
    db.session.commit()

    message = f'Abwesenheit für "{user_name}" ({date_range}) wurde gelöscht.'
    return_to = get_return_url('absences.list_absences')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/occurrence/<date_str>')
def occurrence_detail(id, date_str):
    """Display details for a specific occurrence of a recurring absence."""
    absence = Absence.query.get_or_404(id)
    origin = get_return_info('absences.list_absences', 'Liste')

    if not absence.is_recurring:
        return redirect(url_for('absences.detail', id=id))

    if len(date_str) != 10:
        abort(400, 'Invalid date format')

    try:
        occurrence_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'Invalid date format')

    occurrence_data = recurrence_service.get_occurrence_data(absence, occurrence_date)

    if occurrence_data is None:
        return redirect(url_for('absences.detail', id=id))

    return render_template(
        'absences/occurrence_detail.html',
        absence=absence,
        origin=origin,
        occurrence=occurrence_data,
        occurrence_date=occurrence_date
    )


@bp.route('/<int:id>/occurrence/<date_str>/edit', methods=['GET', 'POST'])
def occurrence_edit(id, date_str):
    """Edit a single occurrence of a recurring absence."""
    absence = Absence.query.get_or_404(id)

    # IDOR protection
    if not can_modify_absence(absence):
        abort(403)

    if not absence.is_recurring:
        return redirect(url_for('absences.edit', id=id))

    if len(date_str) != 10:
        abort(400, 'Invalid date format')

    try:
        occurrence_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'Invalid date format')

    form = OccurrenceEditForm()

    categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()
    if absence.category_id not in {c.id for c in categories}:
        categories = list(categories) + [absence.category]

    form.category_id.choices = [(c.id, c.name) for c in categories]

    users = User.query.filter(
        User.role == UserRole.USER,
        User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED])
    ).order_by(User.name).all()
    substitute_users = [u for u in users if u.id != absence.user_id]
    form.substitute_id.choices = [('', '-- Keine Vertretung --')] + [
        (str(u.id), u.name) for u in substitute_users
    ]

    if request.method == 'GET':
        occurrence_data = recurrence_service.get_occurrence_data(absence, occurrence_date)
        if occurrence_data is None:
            return redirect(url_for('absences.detail', id=id))

        form.category_id.data = occurrence_data['category_id']
        form.substitute_id.data = str(occurrence_data['substitute_id']) if occurrence_data['substitute_id'] else ''
        form.notes.data = occurrence_data['notes']

        if occurrence_data['is_half_day_morning']:
            form.time_type.data = 'half_day_morning'
        elif occurrence_data['is_half_day_afternoon']:
            form.time_type.data = 'half_day_afternoon'
        else:
            form.time_type.data = 'all_day'

    if form.validate_on_submit():
        modifications = form.get_modifications()
        recurrence_service.modify_occurrence(absence, occurrence_date, modifications)
        db.session.commit()

        message = f'Termin am {format_date_for_user(occurrence_date)} wurde geändert.'
        return_to = url_for('absences.occurrence_detail', id=id, date_str=date_str)
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        return redirect(return_to)

    return render_template(
        'absences/occurrence_edit.html',
        form=form,
        absence=absence,
        occurrence_date=occurrence_date
    )


@bp.route('/<int:id>/occurrence/<date_str>/delete', methods=['POST'])
def occurrence_delete(id, date_str):
    """Delete a single occurrence from a recurring absence."""
    absence = Absence.query.get_or_404(id)

    # IDOR protection
    if not can_modify_absence(absence):
        abort(403)

    if not absence.is_recurring:
        message = 'Diese Abwesenheit ist keine Serie.'
        if is_ajax_request():
            return ajax_response(success=False, message=message)
        return redirect(url_for('absences.detail', id=id))

    if len(date_str) != 10:
        abort(400, 'Invalid date format')

    try:
        occurrence_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'Invalid date format')

    recurrence_service.delete_occurrence(absence, occurrence_date)
    db.session.commit()

    message = f'Termin am {format_date_for_user(occurrence_date)} wurde aus der Serie entfernt.'
    return_to = get_return_url('absences.list_absences')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


from . import api  # noqa: E402, F401
