"""User management views."""

from flask import Blueprint, render_template, redirect, request, abort

from core.auth import login_required, current_user
from core.db import db
from utils.navigation import back_url, back_url_focused, origin_link
from utils.response_helpers import ajax_response, is_ajax_request
from utils.pagination import get_pagination
from utils.request_validators import validate_int_param
from utils.validators import normalize_email
from core.settings_manager import settings_manager
from utils.decorators import admin_required, fresh_login_required, manager_required
from modules.auth.models import UserRole, UserStatus
from modules.auth.services import clear_login_attempts_for_username, get_account_lockouts
from .forms import UserCreateForm, UserEditForm
from .services import (
    create_user,
    delete_user,
    validate_last_admin,
    validate_status_change,
    can_toggle_user_status,
    toggle_user_status,
    activate_login_for_managed_user,
    activate_login_with_existing_password,
    can_change_password,
    set_user_password,
    can_end_user_sessions,
    end_user_sessions,
    get_users_list,
    get_user_or_404
)
from modules.absence.services import count_occurrences_by_user, default_list_range


bp = Blueprint('users', __name__, url_prefix='/users')


@bp.before_request
@login_required
def require_login():
    """Require login for all user routes."""
    pass


@bp.route('/')
@manager_required
def list_users():
    """Display list of all users."""
    search = request.args.get('search', '').strip()[:100]

    status_filter = request.args.get('status') or 'active'
    role_filter = request.args.get('role') or 'all'

    try:
        _, total = get_users_list(
            search=search or None,
            status_filter=status_filter,
            role_filter=role_filter if role_filter != 'all' else None
        )
    except ValueError:
        abort(400, 'Invalid filter parameter')

    pagination, redirect_response = get_pagination(total, 'users.list_users')

    if redirect_response:
        return redirect_response

    try:
        users, _ = get_users_list(
            search=search or None,
            status_filter=status_filter,
            role_filter=role_filter if role_filter != 'all' else None,
            page=pagination.page,
            per_page=pagination.per_page
        )
    except ValueError:
        abort(400, 'Invalid filter parameter')

    lockouts = get_account_lockouts([u.username for u in users])
    range_from, range_to = default_list_range()
    absence_counts = count_occurrences_by_user(
        range_from, range_to, [u.id for u in users]
    )

    return render_template(
        'users/list.html',
        users=users,
        search=search,
        status_filter=status_filter,
        role_filter=role_filter,
        lockouts=lockouts,
        absence_counts=absence_counts,
        open_row=validate_int_param('open', min_value=1),
        UserRole=UserRole,
        UserStatus=UserStatus,
        pagination=pagination.to_dict()
    )


@bp.route('/create', methods=['GET', 'POST'])
@manager_required
@fresh_login_required
def create():
    """Create a new user."""
    form = UserCreateForm()

    if not current_user.is_admin:
        form.role.choices = [(UserRole.USER.value, 'User')]
        form.role.data = UserRole.USER

    operation_mode = settings_manager.get('operation_mode')
    if operation_mode == 'single_user':
        hide_password = not current_user.is_admin
    else:
        hide_password = False

    if form.validate_on_submit():
        role = UserRole.USER if not current_user.is_admin else form.role.data

        create_as_managed = (operation_mode == 'single_user' and role == UserRole.USER)

        if not create_as_managed and not form.password.data:
            message = 'Passwort ist erforderlich.'
            if is_ajax_request():
                return ajax_response(success=False, message=message)
            return render_template('users/create.html', form=form, hide_password=False,
                                   password_error='Passwort ist erforderlich.')

        user = create_user(
            username=form.username.data,
            name=form.name.data,
            password=form.password.data if not create_as_managed else None,
            email=form.email.data,
            role=role,
            as_managed=create_as_managed
        )

        db.session.commit()

        message = f'Benutzer "{user.name}" wurde erstellt.'
        return_to = back_url('users.list_users')
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        return redirect(return_to)

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    single_user_mode = (operation_mode == 'single_user')
    return render_template('users/create.html', form=form, hide_password=hide_password,
                           single_user_mode=single_user_mode and current_user.is_admin)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@manager_required
@fresh_login_required
def edit(id):
    """Edit an existing user."""
    user = get_user_or_404(id)

    if not current_user.is_admin and user.role != UserRole.USER:
        abort(403)

    activate_login = request.args.get('activate_login') == '1'

    if activate_login:
        if not current_user.is_admin:
            abort(403)
        if user.status != UserStatus.MANAGED:
            return redirect(origin_link('users.edit', id=id))

    form = UserEditForm(user=user, obj=user)

    if not current_user.is_admin:
        del form.role
        del form.status

    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.email = normalize_email(form.email.data)

        if activate_login and user.status == UserStatus.MANAGED:
            if user.has_real_password:
                message = activate_login_with_existing_password(user)
                db.session.commit()
            else:
                if not form.password.data:
                    message = 'Passwort ist erforderlich um Login zu aktivieren.'
                    if is_ajax_request():
                        return ajax_response(success=False, message=message)
                    return render_template(
                        'users/edit.html', form=form, user=user,
                        activate_login=True, password_required=True,
                        show_password_field=True,
                        password_error='Passwort ist erforderlich um Login zu aktivieren.'
                    )

                message = activate_login_for_managed_user(user, form.password.data)
                db.session.commit()

            return_to = back_url_focused('users.list_users', user.id)
            if is_ajax_request():
                return ajax_response(success=True, message=message, redirect=return_to)
            return redirect(return_to)

        if current_user.is_admin:
            new_role = form.role.data
            new_status = form.status.data
            for is_valid, error in (
                validate_last_admin(user, new_role),
                validate_status_change(current_user, user, new_status, new_role),
            ):
                if not is_valid:
                    if is_ajax_request():
                        return ajax_response(success=False, message=error)
                    abort(400, error)
            if user.status == UserStatus.LOCKED and new_status == UserStatus.ACTIVE:
                clear_login_attempts_for_username(user.username)
            user.role = new_role
            user.status = new_status

        if form.password.data:
            can_change, error = can_change_password(current_user, user)
            if not can_change:
                if is_ajax_request():
                    return ajax_response(success=False, message=error)
                abort(400, error)

            set_user_password(user, form.password.data, require_change=True)

        db.session.commit()

        message = f'Benutzer "{user.name}" wurde aktualisiert.'
        return_to = back_url_focused('users.list_users', user.id)
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=return_to)
        return redirect(return_to)

    password_required = activate_login and not user.has_real_password

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    operation_mode = settings_manager.get('operation_mode')
    show_password_field = user.id != current_user.id and (
        activate_login
        or user.status != UserStatus.MANAGED
        or current_user.is_admin
    )

    single_user_mode = (operation_mode == 'single_user' and current_user.is_admin)
    hide_password_initially = (
        single_user_mode
        and user.status == UserStatus.MANAGED
        and user.role == UserRole.USER
        and not activate_login
    )

    return render_template(
        'users/edit.html', form=form, user=user,
        activate_login=activate_login, password_required=password_required,
        show_password_field=show_password_field,
        single_user_mode=single_user_mode,
        hide_password_initially=hide_password_initially
    )


@bp.route('/<int:id>/toggle-status', methods=['POST'])
@manager_required
@fresh_login_required
def toggle_status(id):
    """Toggle a user's status; MANAGED users are sent to login activation."""
    user = get_user_or_404(id)

    can_toggle, error = can_toggle_user_status(current_user, user)
    if not can_toggle:
        if is_ajax_request():
            return ajax_response(success=False, message=error)
        return redirect(back_url('users.list_users'))

    if user.status == UserStatus.MANAGED:
        if not current_user.is_admin:
            abort(403)
        return_to = origin_link('users.edit', id=user.id, activate_login=1)
        if is_ajax_request():
            return ajax_response(success=True, message='Weiterleitung zur Passwort-Eingabe...', redirect=return_to)
        return redirect(return_to)

    if user.status in [UserStatus.LOCKED, UserStatus.PENDING]:
        if not current_user.is_admin:
            abort(403)

    try:
        _, message = toggle_user_status(user)
    except ValueError as e:
        if is_ajax_request():
            return ajax_response(success=False, message=str(e))
        return redirect(back_url('users.list_users'))

    db.session.commit()

    return_to = back_url_focused('users.list_users', user.id)

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/end-sessions', methods=['POST'])
@manager_required
@fresh_login_required
def end_sessions(id):
    """End all sessions of a user without changing the password."""
    user = get_user_or_404(id)

    allowed, error = can_end_user_sessions(current_user, user)
    if not allowed:
        if is_ajax_request():
            return ajax_response(success=False, message=error)
        return redirect(back_url('users.list_users'))

    end_user_sessions(user)
    db.session.commit()

    message = f'Alle Sitzungen von {user.name} wurden beendet.'
    return_to = back_url_focused('users.list_users', user.id)

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/lift-lockout', methods=['POST'])
@admin_required
@fresh_login_required
def lift_lockout(id):
    """Lift the automatic login lock of an account (Admin only)."""
    user = get_user_or_404(id)

    clear_login_attempts_for_username(user.username)
    db.session.commit()

    message = f'Anmeldesperre von "{user.name}" wurde aufgehoben.'
    return_to = back_url_focused('users.list_users', user.id)

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/delete', methods=['POST'])
@admin_required
@fresh_login_required
def delete(id):
    """Delete a user (Admin only)."""
    user = get_user_or_404(id)

    if user.id == current_user.id:
        message = 'Sie können sich nicht selbst löschen.'
        if is_ajax_request():
            return ajax_response(success=False, message=message)
        return redirect(back_url('users.list_users'))

    user_name = user.name
    delete_user(user)

    message = f'Benutzer "{user_name}" wurde gelöscht.'
    return_to = back_url('users.list_users')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)
