"""Settings views.

Provides user-specific settings management and admin system settings.
"""

from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, jsonify
from flask_login import login_required, current_user

from core.extensions import db
from core.settings_manager import settings_manager
from utils.decorators import login_required_api, admin_required
from utils.session_navigation import is_ajax_request
from utils.response_helpers import ajax_response

bp = Blueprint('settings', __name__, url_prefix='/settings')
from .forms import SettingsForm, AdminSettingsForm
from modules.holidays.services import get_region_choices


def get_date_format_choices() -> list[tuple[str, str]]:
    """Generate date format choices with current year example."""
    year = date.today().year
    return [
        ('DD.MM.YYYY', f'DD.MM.YYYY (z.B. 25.12.{year})'),
        ('YYYY-MM-DD', f'YYYY-MM-DD (z.B. {year}-12-25)')
    ]


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
        current_user.holiday_region = form.holiday_region.data
        current_user.theme = form.theme.data
        current_user.date_format = form.date_format.data
        # 'all' maps to 0 in database (no pagination limit)
        pagination_value = form.pagination.data
        current_user.items_per_page = 0 if pagination_value == 'all' else int(pagination_value)
        current_user.default_text_color = form.default_text_color.data.upper()

        db.session.commit()

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
        # 0 in database maps to 'all' in form
        form.pagination.data = 'all' if current_user.items_per_page == 0 else str(current_user.items_per_page)
        form.default_text_color.data = current_user.default_text_color

    region_choices = get_region_choices()

    settings = {
        'holiday_region': current_user.holiday_region,
        'theme': current_user.theme,
        'date_format': current_user.date_format,
        'pagination': str(current_user.items_per_page),
        'default_text_color': current_user.default_text_color
    }

    return render_template(
        'settings/index.html',
        form=form,
        settings=settings,
        region_choices=region_choices
    )


@bp.route('/system', methods=['GET', 'POST'])
@admin_required
def system_settings():
    """Display and update system settings (Admin only)."""
    form = AdminSettingsForm()
    form.user_default_date_format.choices = get_date_format_choices()

    if form.validate_on_submit():
        # Lockout settings
        settings_manager.set('lockout_threshold', form.lockout_threshold.data)
        settings_manager.set('lockout_duration_minutes', form.lockout_duration_minutes.data)
        settings_manager.set('lockout_delay_enabled', form.lockout_delay_enabled.data)
        settings_manager.set('lockout_delay_base_seconds', form.lockout_delay_base_seconds.data or 0)
        settings_manager.set('lockout_delay_max_seconds', form.lockout_delay_max_seconds.data or 0)
        settings_manager.set('lockout_attempt_retention_hours', form.lockout_attempt_retention_hours.data)
        settings_manager.set('lockout_cleanup_enabled', form.lockout_cleanup_enabled.data)
        settings_manager.set('lockout_cleanup_interval_hours', form.lockout_cleanup_interval_hours.data or 1)

        # Password policy settings
        settings_manager.set('password_min_length', form.password_min_length.data)
        settings_manager.set('password_max_length', form.password_max_length.data)
        settings_manager.set('password_require_uppercase', form.password_require_uppercase.data)
        settings_manager.set('password_require_lowercase', form.password_require_lowercase.data)
        settings_manager.set('password_require_numbers', form.password_require_numbers.data)
        settings_manager.set('password_require_symbols', form.password_require_symbols.data)
        settings_manager.set('password_force_change_on_first_login', form.password_force_change_on_first_login.data)

        # Inactive account settings
        settings_manager.set('inactive_account_auto_disable', form.inactive_account_auto_disable.data)
        settings_manager.set('inactive_account_days', form.inactive_account_days.data or 90)

        # Registration settings
        settings_manager.set('self_registration_enabled', form.self_registration_enabled.data)

        # Operation mode - handle mode switch
        old_mode = settings_manager.get('operation_mode')
        new_mode = form.operation_mode.data
        if old_mode != new_mode:
            settings_manager.set('operation_mode', new_mode)
            if new_mode == 'single_user':
                # Invalidate all USER sessions by incrementing version
                current_version = settings_manager.get('user_session_version')
                settings_manager.set('user_session_version', current_version + 1)

                # Set all USER role accounts to MANAGED
                from modules.auth.models import User, UserRole, UserStatus
                User.query.filter(
                    User.role == UserRole.USER,
                    User.status == UserStatus.ACTIVE
                ).update({User.status: UserStatus.MANAGED})
                db.session.commit()

        # User default settings
        settings_manager.set('user_default_theme', form.user_default_theme.data)
        settings_manager.set('user_default_date_format', form.user_default_date_format.data)
        settings_manager.set('user_default_items_per_page', form.user_default_items_per_page.data)
        settings_manager.set('user_default_holiday_region', form.user_default_holiday_region.data)
        settings_manager.set('user_default_text_color', form.user_default_text_color.data.upper())

        # Commit all settings changes and increment version once
        settings_manager.flush()

        if is_ajax_request():
            return ajax_response(
                success=True,
                message='Systemeinstellungen wurden gespeichert.',
                redirect=url_for('settings.system_settings')
            )
        return redirect(url_for('settings.system_settings'))

    if request.method == 'GET':
        # Lockout settings
        form.lockout_threshold.data = settings_manager.get('lockout_threshold')
        form.lockout_duration_minutes.data = settings_manager.get('lockout_duration_minutes')
        form.lockout_delay_enabled.data = settings_manager.get('lockout_delay_enabled')
        form.lockout_delay_base_seconds.data = settings_manager.get('lockout_delay_base_seconds')
        form.lockout_delay_max_seconds.data = settings_manager.get('lockout_delay_max_seconds')
        form.lockout_attempt_retention_hours.data = settings_manager.get('lockout_attempt_retention_hours')
        form.lockout_cleanup_enabled.data = settings_manager.get('lockout_cleanup_enabled')
        form.lockout_cleanup_interval_hours.data = settings_manager.get('lockout_cleanup_interval_hours')

        # Password policy settings
        form.password_min_length.data = settings_manager.get('password_min_length')
        form.password_max_length.data = settings_manager.get('password_max_length')
        form.password_require_uppercase.data = settings_manager.get('password_require_uppercase')
        form.password_require_lowercase.data = settings_manager.get('password_require_lowercase')
        form.password_require_numbers.data = settings_manager.get('password_require_numbers')
        form.password_require_symbols.data = settings_manager.get('password_require_symbols')
        form.password_force_change_on_first_login.data = settings_manager.get('password_force_change_on_first_login')

        # Inactive account settings
        form.inactive_account_auto_disable.data = settings_manager.get('inactive_account_auto_disable')
        form.inactive_account_days.data = settings_manager.get('inactive_account_days')

        # Registration settings
        form.self_registration_enabled.data = settings_manager.get('self_registration_enabled')

        # Operation mode
        form.operation_mode.data = settings_manager.get('operation_mode')

        # User default settings
        form.user_default_theme.data = settings_manager.get('user_default_theme')
        form.user_default_date_format.data = settings_manager.get('user_default_date_format')
        form.user_default_items_per_page.data = settings_manager.get('user_default_items_per_page')
        form.user_default_holiday_region.data = settings_manager.get('user_default_holiday_region')
        form.user_default_text_color.data = settings_manager.get('user_default_text_color')

    return render_template('settings/system.html', form=form)


@bp.route('/api/theme', methods=['POST'])
@login_required_api
def api_set_theme():
    """API endpoint to set theme (for JavaScript theme switcher)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    theme = data.get('theme', 'light')
    if theme in ('light', 'dark', 'auto'):
        current_user.theme = theme
        db.session.commit()
        return jsonify({'data': {'theme': theme}})
    return jsonify({'error': 'Invalid theme'}), 400


@bp.route('/api/theme', methods=['GET'])
@login_required_api
def api_get_theme():
    """API endpoint to get current theme."""
    return jsonify({'data': {'theme': current_user.theme}})
