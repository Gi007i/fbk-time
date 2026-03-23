"""Settings views.

Provides user-specific settings management and admin system settings.
"""

from flask import Blueprint, render_template, redirect, url_for, request
from flask_login import login_required, current_user

from utils.decorators import login_required_api, admin_required, fresh_session_required
from utils.session_navigation import is_ajax_request
from utils.response_helpers import ajax_response, api_success, api_error
from .forms import SettingsForm, AdminSettingsForm
from .services import (
    get_date_format_choices,
    update_user_settings,
    update_system_settings,
    set_user_theme,
    get_current_settings
)
from modules.holidays.services import get_region_choices

bp = Blueprint('settings', __name__, url_prefix='/settings')


@bp.before_request
@login_required
def require_login():
    """Require login for all settings routes."""
    pass


@bp.route('/', methods=['GET', 'POST'])
def index():
    """Display and update user settings."""
    form = SettingsForm()
    form.date_format.choices = get_date_format_choices()

    if form.validate_on_submit():
        update_user_settings(
            holiday_region=form.holiday_region.data,
            theme=form.theme.data,
            date_format=form.date_format.data,
            pagination=form.pagination.data,
            default_text_color=form.default_text_color.data
        )

        if is_ajax_request():
            return ajax_response(
                success=True,
                message='Einstellungen wurden gespeichert.',
                redirect=url_for('settings.index')
            )
        return redirect(url_for('settings.index'))

    if request.method == 'GET':
        form.holiday_region.data = current_user.holiday_region
        form.theme.data = current_user.theme
        form.date_format.data = current_user.date_format
        form.pagination.data = current_user.items_per_page
        form.default_text_color.data = current_user.default_text_color

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    region_choices = get_region_choices()
    settings = get_current_settings()

    return render_template(
        'settings/index.html',
        form=form,
        settings=settings,
        region_choices=region_choices
    )


@bp.route('/system', methods=['GET', 'POST'])
@fresh_session_required
@admin_required
def system_settings():
    """Display and update system settings (Admin only)."""
    from core.settings_manager import settings_manager

    form = AdminSettingsForm()
    form.user_default_date_format.choices = get_date_format_choices()

    if form.validate_on_submit():
        update_system_settings(
            lockout_threshold=form.lockout_threshold.data,
            lockout_duration_minutes=form.lockout_duration_minutes.data,
            lockout_delay_enabled=form.lockout_delay_enabled.data,
            lockout_delay_base_seconds=form.lockout_delay_base_seconds.data or 0,
            lockout_delay_max_seconds=form.lockout_delay_max_seconds.data or 0,
            lockout_attempt_retention_hours=form.lockout_attempt_retention_hours.data,
            lockout_cleanup_enabled=form.lockout_cleanup_enabled.data,
            lockout_cleanup_interval_hours=form.lockout_cleanup_interval_hours.data or 1,
            password_min_length=form.password_min_length.data,
            password_max_length=form.password_max_length.data,
            password_require_uppercase=form.password_require_uppercase.data,
            password_require_lowercase=form.password_require_lowercase.data,
            password_require_numbers=form.password_require_numbers.data,
            password_require_symbols=form.password_require_symbols.data,
            password_force_change_on_first_login=form.password_force_change_on_first_login.data,
            inactive_account_auto_disable=form.inactive_account_auto_disable.data,
            inactive_account_days=form.inactive_account_days.data or 90,
            self_registration_enabled=form.self_registration_enabled.data,
            operation_mode=form.operation_mode.data,
            user_default_theme=form.user_default_theme.data,
            user_default_date_format=form.user_default_date_format.data,
            user_default_items_per_page=form.user_default_items_per_page.data,
            user_default_holiday_region=form.user_default_holiday_region.data,
            user_default_text_color=form.user_default_text_color.data
        )

        if is_ajax_request():
            return ajax_response(
                success=True,
                message='Systemeinstellungen wurden gespeichert.',
                redirect=url_for('settings.system_settings')
            )
        return redirect(url_for('settings.system_settings'))

    if request.method == 'GET':
        form.lockout_threshold.data = settings_manager.get('lockout_threshold')
        form.lockout_duration_minutes.data = settings_manager.get('lockout_duration_minutes')
        form.lockout_delay_enabled.data = settings_manager.get('lockout_delay_enabled')
        form.lockout_delay_base_seconds.data = settings_manager.get('lockout_delay_base_seconds')
        form.lockout_delay_max_seconds.data = settings_manager.get('lockout_delay_max_seconds')
        form.lockout_attempt_retention_hours.data = settings_manager.get('lockout_attempt_retention_hours')
        form.lockout_cleanup_enabled.data = settings_manager.get('lockout_cleanup_enabled')
        form.lockout_cleanup_interval_hours.data = settings_manager.get('lockout_cleanup_interval_hours')

        form.password_min_length.data = settings_manager.get('password_min_length')
        form.password_max_length.data = settings_manager.get('password_max_length')
        form.password_require_uppercase.data = settings_manager.get('password_require_uppercase')
        form.password_require_lowercase.data = settings_manager.get('password_require_lowercase')
        form.password_require_numbers.data = settings_manager.get('password_require_numbers')
        form.password_require_symbols.data = settings_manager.get('password_require_symbols')
        form.password_force_change_on_first_login.data = settings_manager.get('password_force_change_on_first_login')

        form.inactive_account_auto_disable.data = settings_manager.get('inactive_account_auto_disable')
        form.inactive_account_days.data = settings_manager.get('inactive_account_days')

        form.self_registration_enabled.data = settings_manager.get('self_registration_enabled')

        form.operation_mode.data = settings_manager.get('operation_mode')

        form.user_default_theme.data = settings_manager.get('user_default_theme')
        form.user_default_date_format.data = settings_manager.get('user_default_date_format')
        form.user_default_items_per_page.data = settings_manager.get('user_default_items_per_page')
        form.user_default_holiday_region.data = settings_manager.get('user_default_holiday_region')
        form.user_default_text_color.data = settings_manager.get('user_default_text_color')

    if request.method == 'POST' and is_ajax_request():
        errors = {field.name: field.errors[0] for field in form if field.errors}
        first_error = next(iter(errors.values()), 'Validierungsfehler')
        return ajax_response(success=False, message=first_error, errors=errors)

    return render_template('settings/system.html', form=form)


@bp.route('/api/theme', methods=['POST'])
@login_required_api
def api_set_theme():
    """API endpoint to set theme (for JavaScript theme switcher)."""
    data = request.get_json()
    if not data:
        return api_error('No data provided')

    theme = data.get('theme', 'light')
    error = set_user_theme(theme)

    if error:
        return api_error(error)

    return api_success(data={'theme': theme})


@bp.route('/api/theme', methods=['GET'])
@login_required_api
def api_get_theme():
    """API endpoint to get current theme."""
    return api_success(data={'theme': current_user.theme})
