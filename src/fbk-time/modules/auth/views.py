"""Authentication views.

Provides login, logout, and registration routes with security protections.
"""

from argon2.exceptions import VerifyMismatchError, InvalidHashError
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify
from flask_login import login_required, current_user, login_user

from core.extensions import db
from core.settings_manager import settings_manager
from utils.decorators import login_required_api
from utils.navigation import is_safe_redirect_url
from utils.response_helpers import is_ajax_request
from .forms import LoginForm, RegistrationForm, ChangePasswordForm
from .models import UserRole
from .services import (
    authenticate_user,
    login_user_session,
    logout_user_session,
    is_login_throttled,
    record_failed_attempt,
    clear_failed_attempts,
    clear_failed_ip_attempts,
    is_ip_throttled,
    record_failed_ip_attempt,
    hash_password,
    initialize_session,
    ph,
    register_pending_user
)

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.before_app_request
def check_force_password_change():
    """Enforce password change and session version before allowing access.

    Checks:
    1. Session version for USER role - logout if version mismatch
    2. Force password change redirect for all authenticated users

    Redirects authenticated users with force_password_change=True to the
    change-password page, except for logout and change-password endpoints.
    API requests receive JSON error response instead of redirect.
    """
    if not current_user.is_authenticated:
        return None

    # Check session version for USER role (invalidated on mode switch to single_user)
    if current_user.role == UserRole.USER:
        session_version = session.get('_session_version')
        current_version = settings_manager.get('user_session_version')
        if session_version is None or session_version != current_version:
            logout_user_session()
            if request.endpoint in ('auth.login', 'auth.logout', 'auth.register'):
                return None
            if '/api/' in request.path or is_ajax_request():
                return jsonify({
                    'error': 'Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.',
                    'redirect': url_for('auth.login'),
                }), 401
            return redirect(url_for('auth.login'))

    if not current_user.force_password_change:
        return None

    allowed_endpoints = ('auth.change_password', 'auth.logout', 'static')
    if request.endpoint in allowed_endpoints:
        return None

    if '/api/' in request.path or is_ajax_request():
        from utils.response_helpers import api_error
        return api_error('Passwortänderung erforderlich.', status_code=403)

    return redirect(url_for('auth.change_password'))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login with account lockout protection."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    form = LoginForm()
    self_registration_enabled = settings_manager.get('self_registration_enabled')

    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        password = form.password.data
        client_ip = request.remote_addr

        remaining = max(
            is_login_throttled(username),
            is_ip_throttled(client_ip)
        )
        if remaining > 0:
            if remaining >= 60:
                wait_msg = f'{(remaining + 59) // 60} Minuten'
            else:
                wait_msg = f'{remaining} Sekunden'
            flash(f'Zu viele Fehlversuche. Bitte warten Sie {wait_msg}.', 'danger')
            return render_template(
                'auth/login.html',
                form=form,
                self_registration_enabled=self_registration_enabled
            )

        user, error_message = authenticate_user(username, password)

        if user:
            clear_failed_attempts(username)
            clear_failed_ip_attempts(client_ip)
            login_user_session(user, remember=form.remember.data)

            if user.force_password_change:
                flash('Bitte ändern Sie Ihr Passwort.', 'warning')
                return redirect(url_for('auth.change_password'))

            flash('Erfolgreich angemeldet.', 'success')

            next_page = request.args.get('next')
            if not is_safe_redirect_url(next_page):
                next_page = url_for('dashboard.index')
            return redirect(next_page)

        record_failed_attempt(username)
        record_failed_ip_attempt(client_ip)
        flash('Ungültiger Benutzername oder Passwort.', 'danger')

    return render_template(
        'auth/login.html',
        form=form,
        self_registration_enabled=self_registration_enabled
    )


@bp.route('/keepalive', methods=['POST'])
@login_required_api
def keepalive():
    """Refresh the idle timer and report the remaining session time.

    The idle marker is refreshed by the session-lifecycle before-request
    hook that runs ahead of this view; the response only reports how many
    seconds remain (bounded by both the idle and absolute limits) so the
    client can reschedule its warning countdown. Returns 401 via the hook
    when the session has already expired.
    """
    from core.session_lifecycle import remaining_session_seconds
    return jsonify({'remaining_seconds': remaining_session_seconds()})


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """Handle user logout via POST to prevent CSRF logout attacks."""
    logout_user_session()
    flash('Sie wurden abgemeldet.', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/register', methods=['GET', 'POST'])
def register():
    """Handle user self-registration (when enabled)."""
    if not settings_manager.get('self_registration_enabled'):
        flash('Die Selbstregistrierung ist deaktiviert.', 'warning')
        return redirect(url_for('auth.login'))

    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    form = RegistrationForm()

    if form.validate_on_submit():
        register_pending_user(
            username=form.username.data,
            name=form.name.data,
            password=form.password.data,
            email=form.email.data
        )
        db.session.commit()

        flash(
            'Registrierung erfolgreich! Ihr Konto muss von einem Administrator '
            'freigeschaltet werden, bevor Sie sich anmelden können.',
            'success'
        )
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form)


@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Handle forced password change."""
    form = ChangePasswordForm()

    if form.validate_on_submit():
        # Verify current password (constant-time)
        try:
            ph.verify(current_user.password_hash, form.current_password.data)
            password_valid = True
        except (VerifyMismatchError, InvalidHashError):
            password_valid = False

        if not password_valid:
            # Simulate successful path timing (verify + hash + overhead)
            hash_password("dummy_password_for_timing")
            flash('Aktuelles Passwort ist falsch.', 'danger')
            return render_template('auth/change_password.html', form=form)

        # Prevent reuse of current password
        if form.current_password.data == form.new_password.data:
            flash('Das neue Passwort darf nicht mit dem aktuellen übereinstimmen.', 'danger')
            return render_template('auth/change_password.html', form=form)

        current_user.password_hash = hash_password(form.new_password.data)
        current_user.force_password_change = False
        current_user.has_real_password = True
        current_user.credential_version += 1
        db.session.commit()

        # Cache user reference and return URL before clearing session
        user = current_user._get_current_object()
        return_url = url_for('dashboard.index')

        # Regenerate session; the version bump above invalidates the old
        # session and remember-me cookies on every other device (see get_id).
        session.clear()
        login_user(user)
        initialize_session(user)

        flash('Passwort erfolgreich geändert.', 'success')
        return redirect(return_url)

    return render_template('auth/change_password.html', form=form)
