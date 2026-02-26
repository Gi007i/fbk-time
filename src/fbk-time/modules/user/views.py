"""User management views."""

from flask import Blueprint, render_template, redirect, url_for, request, abort
from flask_login import login_required, current_user

from core.extensions import db
from utils.session_navigation import is_ajax_request, save_return_url, get_return_url
from utils.response_helpers import ajax_response
from utils.pagination import get_pagination
from utils.validators import validate_password_strength
from core.settings_manager import settings_manager
from utils.decorators import manager_required, admin_required, fresh_session_required
from modules.auth.models import UserRole, UserStatus
from modules.auth.services import get_lockout_status_for_users
from .forms import UserCreateForm, UserEditForm
from .services import (
    create_user,
    validate_last_admin,
    can_toggle_user_status,
    toggle_user_status,
    activate_login_for_managed_user,
    activate_login_with_existing_password,
    can_change_password,
    set_user_password,
    get_users_list,
    get_user_absence_count,
    get_user_or_404
)


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
    save_return_url('Mitarbeitende')
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

    usernames = [u.username for u in users]
    lockout_info = get_lockout_status_for_users(usernames)
    failed_attempts = {k: v['attempt_count'] for k, v in lockout_info.items()}

    return render_template(
        'users/list.html',
        users=users,
        search=search,
        status_filter=status_filter,
        role_filter=role_filter,
        failed_attempts=failed_attempts,
        UserRole=UserRole,
        UserStatus=UserStatus,
        pagination=pagination.to_dict()
    )


@bp.route('/create', methods=['GET', 'POST'])
@manager_required
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
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=url_for('users.list_users'))
        return redirect(url_for('users.list_users'))

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    single_user_mode = (operation_mode == 'single_user')
    return render_template('users/create.html', form=form, hide_password=hide_password,
                           single_user_mode=single_user_mode and current_user.is_admin)


@bp.route('/<int:id>')
@manager_required
def detail(id):
    """Display user details."""
    user = get_user_or_404(id)

    if not current_user.is_admin and user.role != UserRole.USER:
        abort(403)

    absence_count = get_user_absence_count(id)

    return render_template(
        'users/detail.html',
        user=user,
        absence_count=absence_count,
        UserRole=UserRole,
        UserStatus=UserStatus
    )


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@manager_required
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
            return redirect(url_for('users.edit', id=id))

    form = UserEditForm(user=user, obj=user)

    if not current_user.is_admin:
        del form.role
        del form.status

    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.email = form.email.data.strip().lower() if form.email.data else None

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

                is_valid, error_msg = validate_password_strength(form.password.data)
                if not is_valid:
                    if is_ajax_request():
                        return ajax_response(success=False, message=error_msg)
                    return render_template(
                        'users/edit.html', form=form, user=user,
                        activate_login=True, password_required=True,
                        show_password_field=True,
                        password_error=error_msg
                    )

                message = activate_login_for_managed_user(user, form.password.data)
                db.session.commit()

            if is_ajax_request():
                return ajax_response(success=True, message=message, redirect=url_for('users.list_users'))
            return redirect(url_for('users.list_users'))

        if current_user.is_admin:
            if hasattr(form, 'role'):
                new_role = form.role.data
                is_valid, error = validate_last_admin(user, new_role)
                if not is_valid:
                    if is_ajax_request():
                        return ajax_response(success=False, message=error)
                    abort(400, error)
                user.role = new_role

            if hasattr(form, 'status'):
                new_status = form.status.data
                if new_status == UserStatus.MANAGED and user.role in [UserRole.ADMIN, UserRole.MANAGER]:
                    message = 'Admin und Manager können nicht auf MANAGED gesetzt werden.'
                    if is_ajax_request():
                        return ajax_response(success=False, message=message)
                    abort(400, message)
                user.status = new_status

        if form.password.data:
            can_change, error = can_change_password(current_user, user)
            if not can_change:
                if is_ajax_request():
                    return ajax_response(success=False, message=error)
                abort(400, error)

            is_valid, error_msg = validate_password_strength(form.password.data)
            if not is_valid:
                if is_ajax_request():
                    return ajax_response(success=False, message=error_msg)
                return render_template(
                    'users/edit.html', form=form, user=user,
                    activate_login=False, password_required=False,
                    show_password_field=True, password_error=error_msg
                )

            set_user_password(user, form.password.data, by_admin=(user.id != current_user.id))

        db.session.commit()

        message = f'Benutzer "{user.name}" wurde aktualisiert.'
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=url_for('users.list_users'))
        return redirect(url_for('users.list_users'))

    password_required = activate_login and not user.has_real_password

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    operation_mode = settings_manager.get('operation_mode')
    show_password_field = (
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
def toggle_status(id):
    """Toggle user status between ACTIVE and DISABLED."""
    user = get_user_or_404(id)

    can_toggle, error = can_toggle_user_status(current_user, user)
    if not can_toggle:
        if is_ajax_request():
            return ajax_response(success=False, message=error)
        return redirect(get_return_url('users.list_users'))

    if not current_user.is_admin and user.role != UserRole.USER:
        abort(403)

    if user.status == UserStatus.MANAGED:
        if not current_user.is_admin:
            abort(403)
        return_to = url_for('users.edit', id=user.id, activate_login=1)
        if is_ajax_request():
            return ajax_response(success=True, message='Weiterleitung zur Passwort-Eingabe...', redirect=return_to)
        return redirect(return_to)

    if user.status in [UserStatus.LOCKED, UserStatus.PENDING]:
        if not current_user.is_admin:
            abort(403)

    try:
        new_status, message = toggle_user_status(user, current_user)
    except ValueError as e:
        if is_ajax_request():
            return ajax_response(success=False, message=str(e))
        return redirect(get_return_url('users.list_users'))

    db.session.commit()

    return_to = get_return_url('users.list_users')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/delete', methods=['POST'])
@fresh_session_required
@admin_required
def delete(id):
    """Delete a user (Admin only)."""
    user = get_user_or_404(id)

    if user.id == current_user.id:
        message = 'Sie können sich nicht selbst löschen.'
        if is_ajax_request():
            return ajax_response(success=False, message=message)
        return redirect(get_return_url('users.list_users'))

    user_name = user.name
    db.session.delete(user)
    db.session.commit()

    message = f'Benutzer "{user_name}" wurde gelöscht.'
    return_to = get_return_url('users.list_users')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)
