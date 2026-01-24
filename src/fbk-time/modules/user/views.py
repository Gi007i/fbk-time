"""User management views."""

from flask import Blueprint, render_template, redirect, url_for, request, abort, jsonify
from flask_login import login_required, current_user

from core.extensions import db
from utils.session_navigation import is_ajax_request, save_return_url, get_return_url
from utils.response_helpers import ajax_response
from core.settings_manager import settings_manager
from modules.auth.models import User, UserRole, UserStatus, LoginAttempt
from modules.auth.services import hash_password
from utils.decorators import manager_required, admin_required

from .forms import UserCreateForm, UserEditForm


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

    query = User.query

    if search:
        query = query.filter(
            db.or_(
                User.name.icontains(search, autoescape=True),
                User.username.icontains(search, autoescape=True),
                User.email.icontains(search, autoescape=True)
            )
        )

    if status_filter == 'active':
        query = query.filter(User.status.in_([UserStatus.ACTIVE, UserStatus.MANAGED]))
    elif status_filter != 'all':
        try:
            status_enum = UserStatus(status_filter)
            query = query.filter(User.status == status_enum)
        except ValueError:
            abort(400, 'Invalid status filter')

    if role_filter != 'all':
        try:
            role_enum = UserRole(role_filter)
            query = query.filter(User.role == role_enum)
        except ValueError:
            abort(400, 'Invalid role filter')

    per_page = current_user.items_per_page
    total = query.count()

    # per_page == 0 means show all (no pagination)
    if per_page == 0:
        page = 1
        total_pages = 1
        users = query.order_by(User.name).all()
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
            return redirect(url_for('users.list_users', **args))

        users = query.order_by(User.name).offset((page - 1) * per_page).limit(per_page).all()

    usernames = [u.username for u in users]
    attempts = LoginAttempt.query.filter(LoginAttempt.identifier.in_(usernames)).all()
    failed_attempts = {a.identifier: a.attempt_count for a in attempts}

    return render_template(
        'users/list.html',
        users=users,
        search=search,
        status_filter=status_filter,
        role_filter=role_filter,
        failed_attempts=failed_attempts,
        UserRole=UserRole,
        UserStatus=UserStatus,
        pagination={
            'page': page,
            'total': total,
            'total_pages': total_pages,
            'has_prev': page > 1 if per_page > 0 else False,
            'has_next': page < total_pages if per_page > 0 else False
        }
    )


@bp.route('/create', methods=['GET', 'POST'])
@manager_required
def create():
    """Create a new user."""
    form = UserCreateForm()

    # Manager can only create USER role
    if not current_user.is_admin:
        form.role.choices = [(UserRole.USER.value, 'User')]
        form.role.data = UserRole.USER

    # Determine if password field should be hidden
    # Single-user mode: Hidden for USER role (MANAGED), shown for Admin/Manager
    operation_mode = settings_manager.get('operation_mode')
    if operation_mode == 'single_user':
        # Admin sees password field only when Admin/Manager role selected (via JS)
        # Manager always creates MANAGED users
        hide_password = not current_user.is_admin
    else:
        hide_password = False

    if form.validate_on_submit():
        # Manager always creates USER role
        role = UserRole.USER if not current_user.is_admin else form.role.data

        # In single-user mode, USER role becomes MANAGED (no password)
        create_as_managed = (operation_mode == 'single_user' and role == UserRole.USER)

        # Validate password is provided for non-managed users
        # Password strength is validated by WTForms
        if not create_as_managed and not form.password.data:
            message = 'Passwort ist erforderlich.'
            if is_ajax_request():
                return ajax_response(success=False, message=message)
            return render_template('users/create.html', form=form, hide_password=False,
                                   password_error='Passwort ist erforderlich.')

        if create_as_managed:
            # Generate random unusable password hash
            import secrets
            password_hash = hash_password(secrets.token_hex(32))
            status = UserStatus.MANAGED
            force_pwd_change = False
            has_real_pwd = False
        else:
            password_hash = hash_password(form.password.data)
            status = UserStatus.ACTIVE
            force_pwd_change = settings_manager.get('password_force_change_on_first_login')
            has_real_pwd = True

        user = User(
            username=form.username.data.strip().lower(),
            password_hash=password_hash,
            name=form.name.data.strip(),
            email=form.email.data.strip().lower() if form.email.data else None,
            role=role,
            status=status,
            force_password_change=force_pwd_change,
            has_real_password=has_real_pwd,
            theme=settings_manager.get('user_default_theme'),
            date_format=settings_manager.get('user_default_date_format'),
            items_per_page=settings_manager.get('user_default_items_per_page'),
            holiday_region=settings_manager.get('user_default_holiday_region'),
            default_text_color=settings_manager.get('user_default_text_color')
        )

        db.session.add(user)
        db.session.commit()

        message = f'Benutzer "{user.name}" wurde erstellt.'
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=url_for('users.list_users'))
        return redirect(url_for('users.list_users'))

    # Handle AJAX validation errors
    if is_ajax_request() and form.errors:
        error_msg = next(iter(form.errors.values()))[0]
        return ajax_response(success=False, message=error_msg)

    # Pass info to template for conditional password field display
    single_user_mode = (operation_mode == 'single_user')
    return render_template('users/create.html', form=form, hide_password=hide_password,
                           single_user_mode=single_user_mode and current_user.is_admin)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@manager_required
def edit(id):
    """Edit an existing user."""
    user = User.query.get_or_404(id)

    # Manager can only edit USER role accounts
    if not current_user.is_admin and user.role != UserRole.USER:
        abort(403)

    # Check if this is a login activation request
    activate_login = request.args.get('activate_login') == '1'

    # Only allow activate_login for MANAGED users by admin
    if activate_login:
        if not current_user.is_admin:
            abort(403)
        if user.status != UserStatus.MANAGED:
            # Not a MANAGED user, redirect to normal edit
            return redirect(url_for('users.edit', id=id))

    form = UserEditForm(user=user, obj=user)

    # Manager cannot edit roles or status
    if not current_user.is_admin:
        del form.role
        del form.status

    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.email = form.email.data.strip().lower() if form.email.data else None

        # Handle login activation for MANAGED users
        if activate_login and user.status == UserStatus.MANAGED:
            if user.has_real_password:
                # User already has a real password, just change status
                # Force password change for security (password may be old/compromised)
                user.status = UserStatus.ACTIVE
                user.force_password_change = True
                db.session.commit()
                message = f'Login für "{user.name}" wurde aktiviert.'
            else:
                # User needs a password
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
                # Validate password strength
                from utils.validators import validate_password_strength
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
                user.password_hash = hash_password(form.password.data)
                user.status = UserStatus.ACTIVE
                user.has_real_password = True
                user.force_password_change = True
                db.session.commit()
                message = f'Login für "{user.name}" wurde aktiviert.'
            if is_ajax_request():
                return ajax_response(success=True, message=message, redirect=url_for('users.list_users'))
            return redirect(url_for('users.list_users'))

        # Only admin can change role and status
        if current_user.is_admin:
            if hasattr(form, 'role'):
                new_role = form.role.data
                # Last admin protection: prevent removing admin role if last admin
                if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
                    admin_count = User.query.filter_by(
                        role=UserRole.ADMIN, status=UserStatus.ACTIVE
                    ).count()
                    if admin_count <= 1:
                        message = 'Der letzte aktive Admin kann seine Rolle nicht ändern.'
                        if is_ajax_request():
                            return ajax_response(success=False, message=message)
                        abort(400, message)
                user.role = new_role

            if hasattr(form, 'status'):
                new_status = form.status.data
                # Admin/Manager cannot be set to MANAGED
                if new_status == UserStatus.MANAGED and user.role in [UserRole.ADMIN, UserRole.MANAGER]:
                    message = 'Admin und Manager können nicht auf MANAGED gesetzt werden.'
                    if is_ajax_request():
                        return ajax_response(success=False, message=message)
                    abort(400, message)
                user.status = new_status

        # Password change: Defense in depth - verify authorization again
        # Cannot change password for MANAGED users via normal edit
        if form.password.data:
            if user.status == UserStatus.MANAGED:
                message = 'Passwort kann für MANAGED User nicht geändert werden. Erst Login aktivieren.'
                if is_ajax_request():
                    return ajax_response(success=False, message=message)
                abort(400, message)
            if not current_user.is_admin and user.role != UserRole.USER:
                abort(403)
            # Validate password strength
            from utils.validators import validate_password_strength
            is_valid, error_msg = validate_password_strength(form.password.data)
            if not is_valid:
                if is_ajax_request():
                    return ajax_response(success=False, message=error_msg)
                return render_template(
                    'users/edit.html', form=form, user=user,
                    activate_login=False, password_required=False,
                    show_password_field=True, password_error=error_msg
                )
            user.password_hash = hash_password(form.password.data)
            user.has_real_password = True
            # Force password change when admin/manager sets OTHER user's password
            # Don't force change when user changes their own password
            if user.id != current_user.id:
                user.force_password_change = True

        db.session.commit()

        message = f'Benutzer "{user.name}" wurde aktualisiert.'
        if is_ajax_request():
            return ajax_response(success=True, message=message, redirect=url_for('users.list_users'))
        return redirect(url_for('users.list_users'))

    # Password required only if activating login AND user has no real password
    password_required = activate_login and not user.has_real_password

    # Handle AJAX validation errors
    if is_ajax_request() and form.errors:
        error_msg = next(iter(form.errors.values()))[0]
        return ajax_response(success=False, message=error_msg)

    # Show password field: activating login, not MANAGED user, or admin
    operation_mode = settings_manager.get('operation_mode')
    show_password_field = (
        activate_login
        or user.status != UserStatus.MANAGED
        or current_user.is_admin
    )

    # Single-user mode: hide password initially if MANAGED user with role=User
    # JS will show it when role is changed to Admin/Manager
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
    user = User.query.get_or_404(id)

    if user.id == current_user.id:
        message = 'Sie können Ihren eigenen Status nicht ändern.'
        if is_ajax_request():
            return ajax_response(success=False, message=message)
        return redirect(get_return_url('users.list_users'))

    # Manager can only toggle USER role accounts
    if not current_user.is_admin and user.role != UserRole.USER:
        abort(403)

    # Manager can only toggle between ACTIVE and DISABLED
    if user.status == UserStatus.ACTIVE:
        user.status = UserStatus.DISABLED
        message = f'Benutzer "{user.name}" wurde deaktiviert.'
    elif user.status == UserStatus.DISABLED:
        user.status = UserStatus.ACTIVE
        message = f'Benutzer "{user.name}" wurde aktiviert.'
    elif user.status == UserStatus.LOCKED:
        # Only admin can unlock
        if not current_user.is_admin:
            abort(403)
        user.status = UserStatus.ACTIVE
        LoginAttempt.query.filter_by(identifier=user.username).delete()
        message = f'Benutzer "{user.name}" wurde entsperrt.'
    elif user.status == UserStatus.PENDING:
        # Only admin can activate pending users
        if not current_user.is_admin:
            abort(403)
        user.status = UserStatus.ACTIVE
        message = f'Benutzer "{user.name}" wurde aktiviert.'
    elif user.status == UserStatus.MANAGED:
        # MANAGED users need password via edit form, redirect there
        if not current_user.is_admin:
            abort(403)
        return_to = url_for('users.edit', id=user.id, activate_login=1)
        if is_ajax_request():
            return ajax_response(success=True, message='Weiterleitung zur Passwort-Eingabe...', redirect=return_to)
        return redirect(return_to)

    db.session.commit()

    return_to = get_return_url('users.list_users')

    if is_ajax_request():
        return ajax_response(success=True, message=message, redirect=return_to)

    return redirect(return_to)


@bp.route('/<int:id>/delete', methods=['POST'])
@admin_required
def delete(id):
    """Delete a user (Admin only)."""
    user = User.query.get_or_404(id)

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
