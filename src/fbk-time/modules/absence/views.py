"""Absence views.

Provides CRUD routes and occurrence management for absences.
"""

from datetime import date, timedelta
from calendar import monthrange

from flask import Blueprint, render_template, redirect, url_for, request, abort
from flask_login import login_required, current_user

from core.extensions import db
from utils.session_navigation import is_ajax_request, save_return_url, get_return_url, get_return_info
from utils.response_helpers import ajax_response
from utils.request_validators import (
    validate_int_param, validate_date_param,
    validate_year_param, validate_month_param, validate_date_string
)
from utils.pagination import paginate_list
from .forms import AbsenceForm, OccurrenceEditForm
from .recurrence import recurrence_service
from .services import (
    can_modify_absence,
    validate_absence_data,
    create_absence,
    update_absence,
    delete_absence,
    modify_occurrence,
    delete_occurrence,
    get_active_users_for_form,
    get_active_categories,
    get_substitute_choices,
    get_absences_list,
    get_absence_history,
    get_absence_or_404,
    get_absence_exception_counts
)
from modules.auth.models import UserRole
from modules.category.services import get_all_categories_ordered
from modules.holidays.services import get_holidays_for_month, count_working_days

bp = Blueprint('absences', __name__, url_prefix='/absences')


@bp.before_request
@login_required
def require_login():
    """Require login for all absence routes."""
    pass


@bp.route('/')
def list_absences():
    """Display expanded absence occurrences with filtering."""
    save_return_url('Liste')

    user_id = validate_int_param('user_id', min_value=1)
    category_id = validate_int_param('category_id', min_value=1)
    has_substitute = request.args.get('has_substitute')
    if has_substitute and has_substitute not in ('yes', 'no'):
        abort(400, 'Invalid has_substitute')

    today = date.today()
    date_from = validate_date_param('date_from', default=today.replace(day=1))

    if date_from is None:
        date_from = today.replace(day=1)

    date_to = validate_date_param('date_to')
    if date_to is None:
        _, last_day = monthrange(date_from.year, date_from.month)
        date_to = date_from.replace(day=last_day)

    absences = get_absences_list(date_from, date_to, user_id, category_id)

    occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, date_from, date_to
    )

    if has_substitute == 'yes':
        occurrences = [o for o in occurrences if o['absence'].substitute_id]
    elif has_substitute == 'no':
        occurrences = [o for o in occurrences if not o['absence'].substitute_id]

    occurrences.sort(key=lambda o: o['date'])

    paginated_occurrences, pagination, redirect_response = paginate_list(
        occurrences, 'absences.list_absences'
    )

    if redirect_response:
        return redirect_response

    users = get_active_users_for_form()
    categories = get_active_categories()

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
        pagination=pagination.to_dict()
    )


@bp.route('/calendar')
def calendar():
    """Display calendar view of absences."""
    save_return_url('Kalender')
    today = date.today()

    year = validate_year_param()
    month = validate_month_param()

    week_start = validate_date_param('week_start')
    if week_start:
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

    user_id = validate_int_param('user_id', min_value=1)
    category_id = validate_int_param('category_id', min_value=1)

    absences = get_absences_list(range_start, range_end, user_id, category_id)
    expanded_occurrences = recurrence_service.get_all_occurrences_for_range(
        absences, range_start, range_end
    )

    holidays = get_holidays_for_month(year, month)
    if week_start.month != month or week_start.year != year:
        holidays.update(get_holidays_for_month(week_start.year, week_start.month))
    if week_end.month != week_start.month:
        holidays.update(get_holidays_for_month(week_end.year, week_end.month))

    users = get_active_users_for_form()
    categories = get_all_categories_ordered()

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

    users = get_active_users_for_form()
    categories = get_active_categories()

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
        if not is_manager and form.user_id.data != current_user.id:
            abort(403)

        time_flags = form.get_time_flags()
        recurrence_data = form.get_recurrence_data()

        is_valid, error, conflicts = validate_absence_data(
            user_id=form.user_id.data,
            category_id=form.category_id.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            substitute_id=form.substitute_id.data,
            time_flags=time_flags,
            recurrence_data=recurrence_data
        )

        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            form.start_date.errors.append(error)
            return render_template('absences/create.html', form=form, is_manager=is_manager)

        try:
            absence, message = create_absence(
                user_id=form.user_id.data,
                category_id=form.category_id.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data,
                time_flags=time_flags,
                recurrence_data=recurrence_data,
                substitute_id=form.substitute_id.data,
                notes=form.notes.data
            )
        except ValueError as e:
            if is_ajax_request():
                return ajax_response(success=False, message=str(e))
            form.start_date.errors.append(str(e))
            return render_template('absences/create.html', form=form, is_manager=is_manager)

        db.session.commit()

        return_to = get_return_url('absences.calendar')
        if is_ajax_request():
            warnings = conflicts.messages if conflicts and conflicts.messages else None
            return ajax_response(success=True, message=message, redirect=return_to, warnings=warnings)
        return redirect(return_to)

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('absences/create.html', form=form, is_manager=is_manager)


@bp.route('/<int:id>')
def detail(id):
    """Display absence details with history."""
    absence = get_absence_or_404(id)
    origin = get_return_info('absences.list_absences', 'Liste')

    history = get_absence_history(id)

    working_days = count_working_days(absence.start_date, absence.end_date)

    recurrence_info = None
    occurrences = []
    if absence.is_recurring:
        occurrence_count = recurrence_service.count_occurrences(absence)
        exception_counts = get_absence_exception_counts(absence)
        recurrence_info = {
            'description': recurrence_service.get_recurrence_description(
                absence.rrule, absence.recurrence_end_date
            ),
            'occurrence_count': occurrence_count,
            'exception_count': exception_counts['exception_count'],
            'deleted_count': exception_counts['deleted_count'],
            'modified_count': exception_counts['modified_count']
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
    absence = get_absence_or_404(id)

    if not can_modify_absence(absence):
        abort(403)

    form = AbsenceForm(obj=absence)

    users = get_active_users_for_form()
    categories = get_active_categories()

    is_manager = current_user.role in (UserRole.ADMIN, UserRole.MANAGER)

    user_ids = {u.id for u in users}
    if absence.user_id not in user_ids:
        users = list(users) + [absence.user]

    category_ids = {c.id for c in categories}
    if absence.category_id not in category_ids:
        categories = list(categories) + [absence.category]

    if is_manager:
        form.user_id.choices = [('', '-- Person auswählen --')] + [(u.id, u.name) for u in users]
    else:
        form.user_id.choices = [(current_user.id, current_user.name)]

    form.category_id.choices = [('', '-- Kategorie auswählen --')] + [(c.id, c.name) for c in categories]

    substitute_users = get_substitute_choices(exclude_user_id=absence.user_id)

    if absence.substitute_id and absence.substitute_id not in {u.id for u in substitute_users}:
        substitute_users.append(absence.substitute)

    form.substitute_id.choices = [('', '-- Keine Vertretung --')] + [
        (str(u.id), u.name) for u in substitute_users
    ]

    if request.method == 'GET':
        form.set_time_type_from_absence(absence)
        form.set_recurrence_from_absence(absence)

    if form.validate_on_submit():
        time_flags = form.get_time_flags()
        recurrence_data = form.get_recurrence_data()

        is_valid, error, conflicts = validate_absence_data(
            user_id=form.user_id.data,
            category_id=form.category_id.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            substitute_id=form.substitute_id.data,
            time_flags=time_flags,
            exclude_absence_id=id,
            recurrence_data=recurrence_data
        )

        if not is_valid:
            if is_ajax_request():
                return ajax_response(success=False, message=error)
            form.start_date.errors.append(error)
            return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)

        try:
            message = update_absence(
                absence=absence,
                user_id=form.user_id.data,
                category_id=form.category_id.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data,
                time_flags=time_flags,
                recurrence_data=recurrence_data,
                substitute_id=form.substitute_id.data,
                notes=form.notes.data
            )
        except ValueError as e:
            if is_ajax_request():
                return ajax_response(success=False, message=str(e))
            form.start_date.errors.append(str(e))
            return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)

        db.session.commit()

        return_to = url_for('absences.detail', id=id)
        if is_ajax_request():
            warnings = conflicts.messages if conflicts and conflicts.messages else None
            return ajax_response(success=True, message=message, redirect=return_to, warnings=warnings)
        return redirect(return_to)

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('absences/edit.html', form=form, absence=absence, is_manager=is_manager)


@bp.route('/<int:id>/delete', methods=['POST'])
def delete(id):
    """Delete an absence record (CASCADE to history)."""
    absence = get_absence_or_404(id)

    if not can_modify_absence(absence):
        abort(403)

    message = delete_absence(absence)
    db.session.commit()

    return_to = get_return_url('absences.list_absences')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/occurrence/<date_str>')
def occurrence_detail(id, date_str):
    """Display details for a specific occurrence of a recurring absence."""
    absence = get_absence_or_404(id)
    origin = get_return_info('absences.list_absences', 'Liste')

    if not absence.is_recurring:
        return redirect(url_for('absences.detail', id=id))

    occurrence_date = validate_date_string(date_str)

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
    absence = get_absence_or_404(id)

    if not can_modify_absence(absence):
        abort(403)

    if not absence.is_recurring:
        return redirect(url_for('absences.edit', id=id))

    occurrence_date = validate_date_string(date_str)

    form = OccurrenceEditForm()

    categories = get_active_categories()
    if absence.category_id not in {c.id for c in categories}:
        categories = list(categories) + [absence.category]

    form.category_id.choices = [(c.id, c.name) for c in categories]

    substitute_users = get_substitute_choices(exclude_user_id=absence.user_id)
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
        try:
            message = modify_occurrence(absence, occurrence_date, modifications)
        except ValueError as e:
            if is_ajax_request():
                return ajax_response(success=False, message=str(e))
            form.category_id.errors.append(str(e))
            return render_template(
                'absences/occurrence_edit.html',
                form=form,
                absence=absence,
                occurrence_date=occurrence_date
            )
        db.session.commit()

        return_to = url_for('absences.occurrence_detail', id=id, date_str=date_str)
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        return redirect(return_to)

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template(
        'absences/occurrence_edit.html',
        form=form,
        absence=absence,
        occurrence_date=occurrence_date
    )


@bp.route('/<int:id>/occurrence/<date_str>/delete', methods=['POST'])
def occurrence_delete(id, date_str):
    """Delete a single occurrence from a recurring absence."""
    absence = get_absence_or_404(id)

    if not can_modify_absence(absence):
        abort(403)

    if not absence.is_recurring:
        message = 'Diese Abwesenheit ist keine Serie.'
        if is_ajax_request():
            return ajax_response(success=False, message=message)
        return redirect(url_for('absences.detail', id=id))

    occurrence_date = validate_date_string(date_str)

    try:
        message = delete_occurrence(absence, occurrence_date)
    except ValueError as e:
        if is_ajax_request():
            return ajax_response(success=False, message=str(e))
        return redirect(url_for('absences.detail', id=id))

    db.session.commit()

    return_to = get_return_url('absences.list_absences')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


from . import api  # noqa: E402, F401
