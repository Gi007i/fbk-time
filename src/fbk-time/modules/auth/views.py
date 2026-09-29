"""Authentication views.

Provides login, re-authentication, logout, and registration routes with
security protections.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify

from core.auth import (
    current_user,
    known_device_id,
    login_required,
    mark_login_fresh,
    remember_device,
)
from core.db import db
from core.session_lifecycle import absolute_remaining_seconds, remaining_session_seconds
from core.settings_manager import settings_manager
from modules.user.services import email_exists, set_user_password, username_exists
from utils.decorators import login_required_api
from utils.navigation import is_safe_redirect_url, start_page_url
from utils.response_helpers import api_error, is_ajax_request
from .forms import LoginForm, ReauthenticateForm, RegistrationForm, ChangePasswordForm
from .models import UserRole
from .services import (
    authenticate_user,
    clear_failed_attempts,
    hash_password,
    login_user_session,
    login_wait_seconds,
    logout_user_session,
    record_failed_login,
    regenerate_session,
    register_pending_user,
    self_registration_available,
    verify_current_password
)

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.before_app_request
def check_force_password_change():
    """Enforce the USER session version and a pending forced password change.

    API requests receive a JSON error instead of a redirect.
    """
    if not current_user.is_authenticated:
        return None

    # The version bumps on a switch to single_user, forcing USER logout.
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
        return api_error('Passwortänderung erforderlich.', status_code=403)

    return redirect(url_for('auth.change_password'))


def _throttle_message(remaining):
    """Return the wait notice for a throttled password entry."""
    if remaining >= 60:
        wait_msg = f'{(remaining + 59) // 60} Minuten'
    else:
        wait_msg = f'{remaining} Sekunden'
    return f'Zu viele Fehlversuche. Bitte warten Sie {wait_msg}.'


def _throttled_password_check(username, check):
    """Run a password check under the login throttling.

    The wait is checked before Argon2 runs. A failure counts on the device
    or account counter and on the IP counter; a success clears the device
    and account counters (the IP counter expires on its own) and marks this
    browser as a known device for the username.

    Args:
        username: Normalized username the password is entered for.
        check: Callable verifying the password; truthy on success.

    Returns:
        Tuple of (result of the check, seconds to wait). A wait above 0
        means the check did not run and the result is None.
    """
    client_ip = request.remote_addr
    device_id = known_device_id(username)
    wait = login_wait_seconds(username, client_ip, device_id)
    if wait > 0:
        return None, wait

    result = check()
    if result:
        clear_failed_attempts(username, device_id)
        remember_device(username)
    else:
        record_failed_login(username, client_ip, device_id)
    return result, 0


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login with account lockout protection."""
    if current_user.is_authenticated:
        return redirect(start_page_url(current_user.start_page))

    form = LoginForm()
    self_registration_enabled = self_registration_available()

    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        password = form.password.data

        user, wait = _throttled_password_check(
            username, lambda: authenticate_user(username, password)
        )
        if wait > 0:
            flash(_throttle_message(wait), 'danger')
            return render_template(
                'auth/login.html',
                form=form,
                self_registration_enabled=self_registration_enabled
            )

        if user:
            login_user_session(user, remember=form.remember.data)

            if user.force_password_change:
                flash('Bitte ändern Sie Ihr Passwort.', 'warning')
                return redirect(url_for('auth.change_password'))

            flash('Erfolgreich angemeldet.', 'success')

            next_page = request.args.get('next')
            if not is_safe_redirect_url(next_page):
                next_page = start_page_url(user.start_page)
            return redirect(next_page)

        flash('Ungültiger Benutzername oder Passwort.', 'danger')

    return render_template(
        'auth/login.html',
        form=form,
        self_registration_enabled=self_registration_enabled
    )


@bp.route('/reauthenticate', methods=['GET', 'POST'])
@login_required
def reauthenticate():
    """Confirm the password before a sensitive action.

    Failures count like login failures, so a hijacked session cannot guess
    the password here without being throttled.
    """
    form = ReauthenticateForm()
    next_page = request.args.get('next')
    if not is_safe_redirect_url(next_page):
        next_page = start_page_url(current_user.start_page)

    if form.validate_on_submit():
        user = current_user._get_current_object()
        valid, wait = _throttled_password_check(
            user.username,
            lambda: verify_current_password(user, form.password.data)
        )
        if wait > 0:
            flash(_throttle_message(wait), 'danger')
            return render_template('auth/reauthenticate.html', form=form)

        if valid:
            mark_login_fresh()
            return redirect(next_page)

        form.password.errors.append('Das Passwort ist falsch.')

    return render_template('auth/reauthenticate.html', form=form)


@bp.route('/keepalive', methods=['POST'])
@login_required_api
def keepalive():
    """Refresh the idle timer and report the remaining session time.

    The session-lifecycle hook refreshes the idle marker (or answers 401)
    before this view runs. The absolute remaining time is reported too
    because a remember-me restore moves that ceiling.
    """
    return jsonify({
        'remaining_seconds': remaining_session_seconds(),
        'absolute_seconds': absolute_remaining_seconds(),
    })


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
    if not self_registration_available():
        flash('Die Selbstregistrierung ist deaktiviert.', 'warning')
        return redirect(url_for('auth.login'))

    if current_user.is_authenticated:
        return redirect(start_page_url(current_user.start_page))

    form = RegistrationForm()

    if form.validate_on_submit():
        # One message for a taken username or e-mail, not bound to a field,
        # so the form does not reveal which of them already exists.
        if (username_exists(form.username.data)
                or (form.email.data and email_exists(form.email.data))):
            # Matches the Argon2 cost of the success path, so the response
            # time does not tell a taken name apart either.
            hash_password(form.password.data)
            flash(
                'Registrierung mit diesen Angaben nicht möglich. Bitte prüfen '
                'Sie Ihre Eingaben oder wenden Sie sich an einen Admin.',
                'danger'
            )
            return render_template('auth/register.html', form=form)

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
    """Handle a password change, throttled like the login."""
    form = ChangePasswordForm()

    if form.validate_on_submit():
        valid, wait = _throttled_password_check(
            current_user.username,
            lambda: verify_current_password(current_user, form.current_password.data)
        )
        if wait > 0:
            flash(_throttle_message(wait), 'danger')
            return render_template('auth/change_password.html', form=form)

        if not valid:
            form.current_password.errors.append('Aktuelles Passwort ist falsch.')
            return render_template('auth/change_password.html', form=form)

        if form.current_password.data == form.new_password.data:
            form.new_password.errors.append(
                'Das neue Passwort darf nicht mit dem aktuellen übereinstimmen.'
            )
            return render_template('auth/change_password.html', form=form)

        # Resolve the proxy before regenerate_session clears the session
        user = current_user._get_current_object()

        set_user_password(user, form.new_password.data, require_change=False)
        db.session.commit()

        return_url = start_page_url(user.start_page)

        regenerate_session(user)
        mark_login_fresh()

        flash('Passwort erfolgreich geändert.', 'success')
        return redirect(return_url)

    return render_template('auth/change_password.html', form=form)
